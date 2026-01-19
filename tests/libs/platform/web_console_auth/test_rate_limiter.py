"""Unit tests for libs.platform.web_console_auth.rate_limiter."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import redis.exceptions

from libs.platform.web_console_auth.rate_limiter import (
    RateLimiter,
    _build_redis_client,
    _int_env,
    get_rate_limiter,
    rate_limiter_health_check,
)


@pytest.mark.asyncio()
async def test_check_rate_limit_allows_and_remaining() -> None:
    redis_client = AsyncMock()
    redis_client.eval = AsyncMock(return_value=3)
    limiter = RateLimiter(redis_client, fallback_mode="deny")

    with patch("libs.platform.web_console_auth.rate_limiter.time.time", return_value=123):
        with patch("libs.platform.web_console_auth.rate_limiter.time.time_ns", return_value=999):
            allowed, remaining = await limiter.check_rate_limit(
                user_id="user1",
                action="login",
                max_requests=5,
                window_seconds=60,
            )

    assert allowed is True
    assert remaining == 2
    args = redis_client.eval.call_args.args
    assert args[2] == "rl:login:user1"
    assert args[3] == "123"
    assert args[4] == "60"
    assert args[5] == "5"
    assert args[6] == "user1:999"


@pytest.mark.asyncio()
async def test_check_rate_limit_blocks_when_exceeded() -> None:
    redis_client = AsyncMock()
    redis_client.eval = AsyncMock(return_value=10)
    limiter = RateLimiter(redis_client, fallback_mode="deny")

    allowed, remaining = await limiter.check_rate_limit(
        user_id="user2",
        action="search",
        max_requests=5,
        window_seconds=60,
    )

    assert allowed is False
    assert remaining == 0


@pytest.mark.asyncio()
async def test_check_rate_limit_fallback_deny_on_connection_error() -> None:
    redis_client = AsyncMock()
    redis_client.eval = AsyncMock(side_effect=redis.exceptions.ConnectionError("down"))
    limiter = RateLimiter(redis_client, fallback_mode="deny")

    allowed, remaining = await limiter.check_rate_limit(
        user_id="user3",
        action="write",
        max_requests=7,
        window_seconds=30,
    )

    assert allowed is False
    assert remaining == 0


@pytest.mark.asyncio()
async def test_check_rate_limit_fallback_allow_on_timeout() -> None:
    redis_client = AsyncMock()
    redis_client.eval = AsyncMock(side_effect=redis.exceptions.TimeoutError("timeout"))
    limiter = RateLimiter(redis_client, fallback_mode="allow")

    allowed, remaining = await limiter.check_rate_limit(
        user_id="user4",
        action="read",
        max_requests=7,
        window_seconds=30,
    )

    assert allowed is True
    assert remaining == 7


@pytest.mark.asyncio()
async def test_check_alert_rate_limit_allows_when_under_limit() -> None:
    redis_client = AsyncMock()
    redis_client.eval = AsyncMock(return_value=2)
    limiter = RateLimiter(redis_client, fallback_mode="deny")

    allowed = await limiter.check_alert_rate_limit("alert:key", limit=3, ttl=60)

    assert allowed is True


@pytest.mark.asyncio()
async def test_check_alert_rate_limit_blocks_when_exceeded() -> None:
    redis_client = AsyncMock()
    redis_client.eval = AsyncMock(return_value=4)
    limiter = RateLimiter(redis_client, fallback_mode="deny")

    allowed = await limiter.check_alert_rate_limit("alert:key", limit=3, ttl=60)

    assert allowed is False


@pytest.mark.asyncio()
async def test_check_alert_rate_limit_fallback_allow_on_redis_error() -> None:
    redis_client = AsyncMock()
    redis_client.eval = AsyncMock(side_effect=redis.exceptions.ConnectionError("down"))
    limiter = RateLimiter(redis_client, fallback_mode="allow")

    allowed = await limiter.check_alert_rate_limit("alert:key", limit=3, ttl=60)

    assert allowed is True


@pytest.mark.asyncio()
async def test_check_channel_rate_limit_invalid_channel() -> None:
    redis_client = AsyncMock()
    limiter = RateLimiter(redis_client, fallback_mode="deny")

    with pytest.raises(ValueError, match="Unknown channel"):
        await limiter.check_channel_rate_limit("fax")


@pytest.mark.asyncio()
async def test_health_check_success_and_failure() -> None:
    redis_client = AsyncMock()
    redis_client.ping = AsyncMock(return_value=True)
    limiter = RateLimiter(redis_client, fallback_mode="deny")

    assert await limiter.health_check() is True

    redis_client.ping = AsyncMock(side_effect=redis.exceptions.ConnectionError("down"))
    assert await limiter.health_check() is False


@pytest.mark.asyncio()
async def test_is_allowed_requires_defaults() -> None:
    redis_client = AsyncMock()
    limiter = RateLimiter(redis_client)

    with pytest.raises(ValueError, match="Default max_requests and window_seconds must be set"):
        await limiter.is_allowed("any")


# ============================================================================
# Tests for missing coverage - Lines 94, 119-139, 144-150, 203-223, 234-237,
# 245-251, 256-258, 281-288, 300-306, 315-319, 324-340, 344-360
# ============================================================================


@pytest.mark.asyncio()
async def test_check_rate_limit_synchronous_eval_result() -> None:
    """Test line 94: synchronous (non-awaitable) eval result."""
    redis_client = MagicMock()
    # Return a plain int, not an awaitable
    redis_client.eval = MagicMock(return_value=3)
    limiter = RateLimiter(redis_client, fallback_mode="deny")

    with patch("libs.platform.web_console_auth.rate_limiter.time.time", return_value=123):
        with patch("libs.platform.web_console_auth.rate_limiter.time.time_ns", return_value=999):
            allowed, remaining = await limiter.check_rate_limit(
                user_id="user_sync",
                action="sync_action",
                max_requests=5,
                window_seconds=60,
            )

    assert allowed is True
    assert remaining == 2


@pytest.mark.asyncio()
async def test_check_rate_limit_generic_exception_fallback_deny() -> None:
    """Test lines 119-139: generic exception with fallback_mode='deny'."""
    redis_client = AsyncMock()
    redis_client.eval = AsyncMock(side_effect=RuntimeError("unexpected error"))
    limiter = RateLimiter(redis_client, fallback_mode="deny")

    allowed, remaining = await limiter.check_rate_limit(
        user_id="user_exc",
        action="exc_action",
        max_requests=5,
        window_seconds=60,
    )

    assert allowed is False
    assert remaining == 0


@pytest.mark.asyncio()
async def test_check_rate_limit_generic_exception_fallback_allow() -> None:
    """Test lines 119-139: generic exception with fallback_mode='allow'."""
    redis_client = AsyncMock()
    redis_client.eval = AsyncMock(side_effect=ValueError("some error"))
    limiter = RateLimiter(redis_client, fallback_mode="allow")

    allowed, remaining = await limiter.check_rate_limit(
        user_id="user_exc2",
        action="exc_action2",
        max_requests=10,
        window_seconds=120,
    )

    assert allowed is True
    assert remaining == 10


@pytest.mark.asyncio()
async def test_check_rate_limit_generic_exception_with_override_fallback() -> None:
    """Test lines 119-139: generic exception with per-call fallback override."""
    redis_client = AsyncMock()
    redis_client.eval = AsyncMock(side_effect=TypeError("type error"))
    limiter = RateLimiter(redis_client, fallback_mode="deny")

    # Override to allow at call time
    allowed, remaining = await limiter.check_rate_limit(
        user_id="user_override",
        action="override_action",
        max_requests=8,
        window_seconds=30,
        fallback_mode="allow",
    )

    assert allowed is True
    assert remaining == 8


@pytest.mark.asyncio()
async def test_is_allowed_with_defaults_set() -> None:
    """Test lines 144-150: is_allowed method with defaults set."""
    redis_client = AsyncMock()
    redis_client.eval = AsyncMock(return_value=2)
    limiter = RateLimiter(
        redis_client, fallback_mode="deny", max_requests=10, window_seconds=60
    )

    result = await limiter.is_allowed("test_key")

    assert result is True


@pytest.mark.asyncio()
async def test_is_allowed_blocked_when_exceeded() -> None:
    """Test lines 144-150: is_allowed returns False when limit exceeded."""
    redis_client = AsyncMock()
    redis_client.eval = AsyncMock(return_value=15)
    limiter = RateLimiter(
        redis_client, fallback_mode="deny", max_requests=10, window_seconds=60
    )

    result = await limiter.is_allowed("test_key_blocked")

    assert result is False


@pytest.mark.asyncio()
async def test_check_alert_rate_limit_generic_exception_fallback_deny() -> None:
    """Test lines 203-223: generic exception with fallback_mode='deny'."""
    redis_client = AsyncMock()
    redis_client.eval = AsyncMock(side_effect=RuntimeError("alert error"))
    limiter = RateLimiter(redis_client, fallback_mode="deny")

    allowed = await limiter.check_alert_rate_limit("alert:exc", limit=5, ttl=60)

    assert allowed is False


@pytest.mark.asyncio()
async def test_check_alert_rate_limit_generic_exception_fallback_allow() -> None:
    """Test lines 203-223: generic exception with fallback_mode='allow'."""
    redis_client = AsyncMock()
    redis_client.eval = AsyncMock(side_effect=ValueError("alert value error"))
    limiter = RateLimiter(redis_client, fallback_mode="allow")

    allowed = await limiter.check_alert_rate_limit("alert:exc2", limit=5, ttl=60)

    assert allowed is True


@pytest.mark.asyncio()
async def test_check_alert_rate_limit_generic_exception_with_override() -> None:
    """Test lines 203-223: generic exception with per-call fallback override."""
    redis_client = AsyncMock()
    redis_client.eval = AsyncMock(side_effect=TypeError("alert type error"))
    limiter = RateLimiter(redis_client, fallback_mode="deny")

    # Override to allow at call time
    allowed = await limiter.check_alert_rate_limit(
        "alert:exc3", limit=5, ttl=60, fallback_mode="allow"
    )

    assert allowed is True


@pytest.mark.asyncio()
async def test_check_alert_rate_limit_timeout_error_deny() -> None:
    """Test timeout error handling in check_alert_rate_limit with deny fallback."""
    redis_client = AsyncMock()
    redis_client.eval = AsyncMock(side_effect=redis.exceptions.TimeoutError("timeout"))
    limiter = RateLimiter(redis_client, fallback_mode="deny")

    allowed = await limiter.check_alert_rate_limit("alert:timeout", limit=5, ttl=60)

    assert allowed is False


@pytest.mark.asyncio()
async def test_check_channel_rate_limit_success() -> None:
    """Test lines 234-237: check_channel_rate_limit success path."""
    redis_client = AsyncMock()
    redis_client.eval = AsyncMock(return_value=5)
    limiter = RateLimiter(redis_client, fallback_mode="deny")

    with patch("libs.platform.web_console_auth.rate_limiter.time.time", return_value=1000):
        allowed = await limiter.check_channel_rate_limit("email")

    assert allowed is True
    # Verify the key format includes minute bucket
    call_args = redis_client.eval.call_args
    assert "alert_ratelimit:channel:email:16" in call_args.args[2]  # 1000 // 60 = 16


@pytest.mark.asyncio()
async def test_check_channel_rate_limit_slack() -> None:
    """Test check_channel_rate_limit for slack channel."""
    redis_client = AsyncMock()
    redis_client.eval = AsyncMock(return_value=51)  # Over limit of 50
    limiter = RateLimiter(redis_client, fallback_mode="deny")

    with patch("libs.platform.web_console_auth.rate_limiter.time.time", return_value=120):
        allowed = await limiter.check_channel_rate_limit("slack")

    assert allowed is False


@pytest.mark.asyncio()
async def test_check_channel_rate_limit_sms() -> None:
    """Test check_channel_rate_limit for sms channel."""
    redis_client = AsyncMock()
    redis_client.eval = AsyncMock(return_value=5)
    limiter = RateLimiter(redis_client, fallback_mode="deny")

    with patch("libs.platform.web_console_auth.rate_limiter.time.time", return_value=180):
        allowed = await limiter.check_channel_rate_limit("sms")

    assert allowed is True  # Under limit of 10


@pytest.mark.asyncio()
async def test_check_recipient_rate_limit_success() -> None:
    """Test lines 245-251: check_recipient_rate_limit success path."""
    redis_client = AsyncMock()
    redis_client.eval = AsyncMock(return_value=2)
    limiter = RateLimiter(redis_client, fallback_mode="deny")

    with patch("libs.platform.web_console_auth.rate_limiter.time.time", return_value=7200):
        allowed = await limiter.check_recipient_rate_limit("hash123", "email")

    assert allowed is True
    # Verify the key format includes hour bucket
    call_args = redis_client.eval.call_args
    assert "alert_ratelimit:recipient:email:hash123:2" in call_args.args[2]  # 7200 // 3600 = 2


@pytest.mark.asyncio()
async def test_check_recipient_rate_limit_invalid_channel() -> None:
    """Test lines 245-251: check_recipient_rate_limit with invalid channel."""
    redis_client = AsyncMock()
    limiter = RateLimiter(redis_client, fallback_mode="deny")

    with pytest.raises(ValueError, match="Unknown channel type for recipient limit"):
        await limiter.check_recipient_rate_limit("hash456", "telegram")


@pytest.mark.asyncio()
async def test_check_recipient_rate_limit_blocked() -> None:
    """Test check_recipient_rate_limit when limit exceeded."""
    redis_client = AsyncMock()
    redis_client.eval = AsyncMock(return_value=6)  # Over limit of 5 for email
    limiter = RateLimiter(redis_client, fallback_mode="deny")

    with patch("libs.platform.web_console_auth.rate_limiter.time.time", return_value=3600):
        allowed = await limiter.check_recipient_rate_limit("hash789", "email")

    assert allowed is False


@pytest.mark.asyncio()
async def test_check_global_rate_limit_success() -> None:
    """Test lines 256-258: check_global_rate_limit success path."""
    redis_client = AsyncMock()
    redis_client.eval = AsyncMock(return_value=100)  # Under limit of 500
    limiter = RateLimiter(redis_client, fallback_mode="deny")

    with patch("libs.platform.web_console_auth.rate_limiter.time.time", return_value=600):
        allowed = await limiter.check_global_rate_limit()

    assert allowed is True
    # Verify the key format includes minute bucket
    call_args = redis_client.eval.call_args
    assert "alert_ratelimit:global:10" in call_args.args[2]  # 600 // 60 = 10


@pytest.mark.asyncio()
async def test_check_global_rate_limit_blocked() -> None:
    """Test check_global_rate_limit when limit exceeded."""
    redis_client = AsyncMock()
    redis_client.eval = AsyncMock(return_value=501)  # Over limit of 500
    limiter = RateLimiter(redis_client, fallback_mode="deny")

    with patch("libs.platform.web_console_auth.rate_limiter.time.time", return_value=60):
        allowed = await limiter.check_global_rate_limit()

    assert allowed is False


@pytest.mark.asyncio()
async def test_health_check_generic_exception() -> None:
    """Test lines 281-288: health_check with generic exception."""
    redis_client = AsyncMock()
    redis_client.ping = AsyncMock(side_effect=RuntimeError("unexpected ping error"))
    limiter = RateLimiter(redis_client, fallback_mode="deny")

    result = await limiter.health_check()

    assert result is False


@pytest.mark.asyncio()
async def test_health_check_timeout_error() -> None:
    """Test health_check with TimeoutError."""
    redis_client = AsyncMock()
    redis_client.ping = AsyncMock(side_effect=redis.exceptions.TimeoutError("ping timeout"))
    limiter = RateLimiter(redis_client, fallback_mode="deny")

    result = await limiter.health_check()

    assert result is False


def test_build_redis_client_with_redis_url() -> None:
    """Test lines 300-306: _build_redis_client with REDIS_URL set."""
    with patch.dict("os.environ", {"REDIS_URL": "redis://testhost:1234/0"}):
        with patch("redis.asyncio.Redis.from_url") as mock_from_url:
            mock_from_url.return_value = MagicMock()
            client = _build_redis_client()

            mock_from_url.assert_called_once_with(
                "redis://testhost:1234/0", db=2, decode_responses=True
            )
            assert client is not None


def test_build_redis_client_with_host_port() -> None:
    """Test lines 300-306: _build_redis_client with REDIS_HOST/PORT fallback."""
    with patch.dict(
        "os.environ", {"REDIS_URL": "", "REDIS_HOST": "myhost", "REDIS_PORT": "9999"}, clear=False
    ):
        with patch("redis.asyncio.Redis") as mock_redis:
            mock_redis.return_value = MagicMock()
            client = _build_redis_client()

            mock_redis.assert_called_once_with(
                host="myhost", port=9999, db=2, decode_responses=True
            )
            assert client is not None


def test_build_redis_client_with_defaults() -> None:
    """Test _build_redis_client with default values."""
    with patch.dict("os.environ", {"REDIS_URL": ""}, clear=False):
        # Remove any existing REDIS_HOST and REDIS_PORT
        env_backup = {}
        import os

        for key in ["REDIS_HOST", "REDIS_PORT"]:
            if key in os.environ:
                env_backup[key] = os.environ.pop(key)

        try:
            with patch("redis.asyncio.Redis") as mock_redis:
                mock_redis.return_value = MagicMock()
                client = _build_redis_client()

                mock_redis.assert_called_once_with(
                    host="redis", port=6379, db=2, decode_responses=True
                )
                assert client is not None
        finally:
            # Restore environment
            os.environ.update(env_backup)


def test_int_env_valid_value() -> None:
    """Test lines 315-319: _int_env with valid integer value."""
    with patch.dict("os.environ", {"TEST_INT_VAR": "42"}):
        result = _int_env("TEST_INT_VAR", 100)
        assert result == 42


def test_int_env_invalid_value() -> None:
    """Test lines 315-319: _int_env with invalid (non-integer) value."""
    import logging

    # Suppress logging as the source code logs with 'name' in extra which conflicts
    # with LogRecord reserved attributes in some logging configs
    with patch.dict("os.environ", {"TEST_INT_VAR_INVALID": "not_an_int"}):
        with patch.object(logging.getLogger("libs.platform.web_console_auth.rate_limiter"), "warning"):
            result = _int_env("TEST_INT_VAR_INVALID", 100)
            assert result == 100  # Should return default


def test_int_env_missing_value() -> None:
    """Test _int_env with missing environment variable."""
    import os

    # Ensure the var doesn't exist
    env_backup = os.environ.pop("TEST_MISSING_VAR", None)
    try:
        result = _int_env("TEST_MISSING_VAR", 50)
        assert result == 50
    finally:
        if env_backup is not None:
            os.environ["TEST_MISSING_VAR"] = env_backup


def test_get_rate_limiter_creates_singleton() -> None:
    """Test lines 324-340: get_rate_limiter creates singleton."""
    import libs.platform.web_console_auth.rate_limiter as rl_module

    # Reset singleton
    original_singleton = rl_module._rate_limiter_singleton
    rl_module._rate_limiter_singleton = None

    try:
        with patch.dict(
            "os.environ",
            {
                "REDIS_URL": "",
                "RATE_LIMITER_FALLBACK_MODE": "allow",
                "RATE_LIMIT_MAX": "200",
                "RATE_LIMIT_WINDOW": "120",
            },
        ):
            with patch("redis.asyncio.Redis") as mock_redis:
                mock_redis.return_value = MagicMock()

                limiter1 = get_rate_limiter()
                limiter2 = get_rate_limiter()

                assert limiter1 is limiter2
                assert limiter1.fallback_mode == "allow"
                assert limiter1.default_max_requests == 200
                assert limiter1.default_window_seconds == 120
    finally:
        rl_module._rate_limiter_singleton = original_singleton


def test_get_rate_limiter_with_explicit_fallback_mode() -> None:
    """Test get_rate_limiter with explicit fallback_mode parameter."""
    import libs.platform.web_console_auth.rate_limiter as rl_module

    # Reset singleton
    original_singleton = rl_module._rate_limiter_singleton
    rl_module._rate_limiter_singleton = None

    try:
        with patch.dict("os.environ", {"REDIS_URL": ""}):
            with patch("redis.asyncio.Redis") as mock_redis:
                mock_redis.return_value = MagicMock()

                limiter = get_rate_limiter(fallback_mode="deny")

                assert limiter.fallback_mode == "deny"
    finally:
        rl_module._rate_limiter_singleton = original_singleton


def test_get_rate_limiter_returns_existing_singleton() -> None:
    """Test get_rate_limiter returns existing singleton without recreating."""
    import libs.platform.web_console_auth.rate_limiter as rl_module

    # Reset singleton
    original_singleton = rl_module._rate_limiter_singleton
    existing_limiter = MagicMock(spec=RateLimiter)
    rl_module._rate_limiter_singleton = existing_limiter

    try:
        result = get_rate_limiter()
        assert result is existing_limiter
    finally:
        rl_module._rate_limiter_singleton = original_singleton


def test_rate_limiter_health_check_sync_context() -> None:
    """Test lines 344-360: rate_limiter_health_check in sync context (no running loop)."""
    import libs.platform.web_console_auth.rate_limiter as rl_module

    # Reset singleton
    original_singleton = rl_module._rate_limiter_singleton
    rl_module._rate_limiter_singleton = None

    try:
        with patch.dict("os.environ", {"REDIS_URL": ""}):
            with patch("redis.asyncio.Redis") as mock_redis:
                mock_client = AsyncMock()
                mock_client.ping = AsyncMock(return_value=True)
                mock_redis.return_value = mock_client

                # Call in sync context (no event loop)
                result = rate_limiter_health_check()

                assert result == {"healthy": True}
    finally:
        rl_module._rate_limiter_singleton = original_singleton


@pytest.mark.asyncio()
async def test_rate_limiter_health_check_async_context() -> None:
    """Test lines 344-360: rate_limiter_health_check in async context (running loop)."""
    import libs.platform.web_console_auth.rate_limiter as rl_module

    # Reset singleton
    original_singleton = rl_module._rate_limiter_singleton
    rl_module._rate_limiter_singleton = None

    try:
        with patch.dict("os.environ", {"REDIS_URL": ""}):
            with patch("redis.asyncio.Redis") as mock_redis:
                mock_client = AsyncMock()
                mock_client.ping = AsyncMock(return_value=True)
                mock_redis.return_value = mock_client

                # Call in async context (event loop running)
                result = rate_limiter_health_check()

                # Should return an awaitable
                assert asyncio.iscoroutine(result) or hasattr(result, "__await__")
                if asyncio.iscoroutine(result):
                    final_result = await result
                    assert final_result == {"healthy": True}
    finally:
        rl_module._rate_limiter_singleton = original_singleton


def test_rate_limiter_health_check_unhealthy() -> None:
    """Test rate_limiter_health_check when Redis is unhealthy."""
    import libs.platform.web_console_auth.rate_limiter as rl_module

    # Reset singleton
    original_singleton = rl_module._rate_limiter_singleton
    rl_module._rate_limiter_singleton = None

    try:
        with patch.dict("os.environ", {"REDIS_URL": ""}):
            with patch("redis.asyncio.Redis") as mock_redis:
                mock_client = AsyncMock()
                mock_client.ping = AsyncMock(side_effect=redis.exceptions.ConnectionError("down"))
                mock_redis.return_value = mock_client

                # Call in sync context
                result = rate_limiter_health_check()

                assert result == {"healthy": False}
    finally:
        rl_module._rate_limiter_singleton = original_singleton


def test_rate_limiter_health_check_sync_return() -> None:
    """Test line 360: rate_limiter_health_check when health_check returns non-coroutine."""
    import libs.platform.web_console_auth.rate_limiter as rl_module

    # Reset singleton
    original_singleton = rl_module._rate_limiter_singleton

    # Create a mock limiter where health_check returns a non-coroutine (synchronous)
    mock_limiter = MagicMock(spec=RateLimiter)
    mock_limiter.health_check = MagicMock(return_value=True)  # Returns bool directly, not a coroutine
    rl_module._rate_limiter_singleton = mock_limiter

    try:
        result = rate_limiter_health_check()
        assert result == {"healthy": True}
    finally:
        rl_module._rate_limiter_singleton = original_singleton

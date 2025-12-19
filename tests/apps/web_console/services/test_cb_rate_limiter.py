"""Tests for CB rate limiter."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from apps.web_console.services.cb_rate_limiter import CBRateLimiter


class TestCBRateLimiter:
    """Tests for CBRateLimiter."""

    @pytest.fixture()
    def mock_redis(self) -> MagicMock:
        """Create mock Redis client."""
        return MagicMock()

    @pytest.fixture()
    def rate_limiter(self, mock_redis: MagicMock) -> CBRateLimiter:
        """Create rate limiter with mock Redis."""
        with patch.dict("os.environ", {"ENVIRONMENT": "test"}):
            return CBRateLimiter(mock_redis)

    def test_first_reset_allowed_with_set_if_not_exists(
        self, rate_limiter: CBRateLimiter, mock_redis: MagicMock
    ) -> None:
        """First reset should be allowed (SET NX succeeds)."""
        # set_if_not_exists returns True when key was set (not exists)
        mock_redis.set_if_not_exists.return_value = True

        result = rate_limiter.check_global(limit=1, window=60)

        assert result is True
        mock_redis.set_if_not_exists.assert_called_once_with(rate_limiter.key, "1", ex=60)
        # eval should not be called for limit=1
        mock_redis.eval.assert_not_called()

    def test_second_reset_within_minute_blocked_with_set_if_not_exists(
        self, rate_limiter: CBRateLimiter, mock_redis: MagicMock
    ) -> None:
        """Second reset within window should be blocked (SET NX fails)."""
        # set_if_not_exists returns False when key already exists
        mock_redis.set_if_not_exists.return_value = False

        result = rate_limiter.check_global(limit=1, window=60)

        assert result is False
        mock_redis.set_if_not_exists.assert_called_once_with(rate_limiter.key, "1", ex=60)

    def test_lua_script_used_for_limit_gt_1(
        self, rate_limiter: CBRateLimiter, mock_redis: MagicMock
    ) -> None:
        """Lua script should be used for limit > 1 (atomic INCR+EXPIRE)."""
        # Lua script returns count after increment
        mock_redis.eval.return_value = 3

        rate_limiter.check_global(limit=5, window=120)

        mock_redis.eval.assert_called_once()
        # set_if_not_exists should not be called for limit > 1
        mock_redis.set_if_not_exists.assert_not_called()

    def test_custom_limit_allows_multiple(
        self, rate_limiter: CBRateLimiter, mock_redis: MagicMock
    ) -> None:
        """Custom limit should allow multiple resets."""
        mock_redis.eval.return_value = 3

        result = rate_limiter.check_global(limit=5, window=60)

        assert result is True

    def test_custom_limit_at_boundary(
        self, rate_limiter: CBRateLimiter, mock_redis: MagicMock
    ) -> None:
        """At limit boundary should still be allowed."""
        mock_redis.eval.return_value = 5

        result = rate_limiter.check_global(limit=5, window=60)

        assert result is True

    def test_custom_limit_exceeded(
        self, rate_limiter: CBRateLimiter, mock_redis: MagicMock
    ) -> None:
        """Over limit should be blocked."""
        mock_redis.eval.return_value = 6

        result = rate_limiter.check_global(limit=5, window=60)

        assert result is False

    def test_lua_script_passes_correct_args(
        self, rate_limiter: CBRateLimiter, mock_redis: MagicMock
    ) -> None:
        """Lua script should receive correct key and window args."""
        mock_redis.eval.return_value = 1

        rate_limiter.check_global(limit=3, window=120)

        # eval(script, numkeys, key, window_as_string)
        call_args = mock_redis.eval.call_args
        assert call_args[0][1] == 1  # numkeys
        assert call_args[0][2] == rate_limiter.key  # key
        assert call_args[0][3] == "120"  # window as string

    def test_key_includes_environment(self, mock_redis: MagicMock) -> None:
        """Rate limit key should be namespaced by environment."""
        with patch.dict("os.environ", {"ENVIRONMENT": "production"}):
            limiter = CBRateLimiter(mock_redis)
            assert "production" in limiter.key
            assert limiter.key == "cb_ratelimit:production:reset:global"

    def test_clear_deletes_rate_limit_key(
        self, rate_limiter: CBRateLimiter, mock_redis: MagicMock
    ) -> None:
        """clear should delete the rate limit key."""
        rate_limiter.clear()

        mock_redis.delete.assert_called_once_with(rate_limiter.key)


class TestCBRateLimiterConcurrency:
    """Tests for concurrent rate limiting scenarios."""

    @pytest.fixture()
    def mock_redis(self) -> MagicMock:
        """Create mock Redis client."""
        return MagicMock()

    def test_concurrent_resets_atomic_check(self, mock_redis: MagicMock) -> None:
        """Only one concurrent reset should pass due to atomic set_if_not_exists.

        This test simulates the scenario where two processes try to reset
        simultaneously. Because SET NX is atomic, only the first one
        will succeed in setting the key.
        """
        with patch.dict("os.environ", {"ENVIRONMENT": "test"}):
            limiter1 = CBRateLimiter(mock_redis)
            limiter2 = CBRateLimiter(mock_redis)

        # Simulate two concurrent set_if_not_exists calls
        # First call succeeds (returns True), second fails (returns False)
        mock_redis.set_if_not_exists.side_effect = [True, False]

        result1 = limiter1.check_global(limit=1, window=60)
        result2 = limiter2.check_global(limit=1, window=60)

        assert result1 is True
        assert result2 is False

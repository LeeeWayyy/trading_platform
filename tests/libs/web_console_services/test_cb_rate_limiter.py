"""Tests for CBRateLimiter."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from libs.web_console_services.cb_rate_limiter import _INCR_WITH_EXPIRE_LUA, CBRateLimiter


@pytest.fixture()
def redis_client() -> MagicMock:
    return MagicMock()


def test_rate_limiter_key_namespaced_by_environment(
    redis_client: MagicMock, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ENVIRONMENT", "paper")

    limiter = CBRateLimiter(redis_client)

    assert limiter.key == "cb_ratelimit:paper:reset:global"


def test_check_global_limit_one_allows(redis_client: MagicMock) -> None:
    redis_client.set_if_not_exists.return_value = True
    limiter = CBRateLimiter(redis_client)

    allowed = limiter.check_global(limit=1, window=120)

    assert allowed is True
    redis_client.set_if_not_exists.assert_called_once_with(limiter.key, "1", ex=120)
    redis_client.eval.assert_not_called()


def test_check_global_limit_one_blocks(redis_client: MagicMock) -> None:
    redis_client.set_if_not_exists.return_value = False
    limiter = CBRateLimiter(redis_client)

    allowed = limiter.check_global(limit=1, window=60)

    assert allowed is False
    redis_client.set_if_not_exists.assert_called_once_with(limiter.key, "1", ex=60)


def test_check_global_limit_multi_uses_lua(redis_client: MagicMock) -> None:
    redis_client.eval.return_value = "2"
    limiter = CBRateLimiter(redis_client)

    allowed = limiter.check_global(limit=2, window=30)

    assert allowed is True
    redis_client.eval.assert_called_once_with(_INCR_WITH_EXPIRE_LUA, 1, limiter.key, "30")


def test_check_global_limit_multi_blocks_when_exceeded(redis_client: MagicMock) -> None:
    redis_client.eval.return_value = 5
    limiter = CBRateLimiter(redis_client)

    allowed = limiter.check_global(limit=3, window=30)

    assert allowed is False


def test_clear_deletes_key(redis_client: MagicMock) -> None:
    limiter = CBRateLimiter(redis_client)

    limiter.clear()

    redis_client.delete.assert_called_once_with(limiter.key)

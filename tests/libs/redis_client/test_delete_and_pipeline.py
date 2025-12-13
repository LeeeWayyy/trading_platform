"""Tests for RedisClient.delete() and pipeline() methods.

These tests use unittest.mock to patch the redis module at the method level,
avoiding module reloads that can interfere with other tests.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

pytest.importorskip("pydantic")


class FakeRedis:
    """Fake Redis client for testing."""

    def __init__(self, *args, **kwargs):
        self.deleted: list[tuple[str, ...]] = []

    def ping(self) -> bool:
        return True

    def delete(self, *keys: str) -> int:
        self.deleted.append(keys)
        return len(keys)

    def pipeline(self, transaction: bool = True) -> SimpleNamespace:
        return SimpleNamespace(transaction=transaction)


class FakeConnectionPool:
    """Fake connection pool for testing."""

    def __init__(self, *args, **kwargs):
        pass

    def disconnect(self) -> None:
        pass


def test_delete_multiple_keys_returns_count():
    """Test that delete() returns count of deleted keys."""
    fake_redis_instance = FakeRedis()

    with patch("libs.redis_client.client.ConnectionPool", FakeConnectionPool):
        with patch("libs.redis_client.client.redis.Redis", return_value=fake_redis_instance):
            from libs.redis_client.client import RedisClient

            client = RedisClient()
            deleted = client.delete("a", "b", "c")
            assert deleted == 3


def test_pipeline_respects_transaction_flag():
    """Test that pipeline() respects the transaction flag."""
    fake_redis_instance = FakeRedis()

    with patch("libs.redis_client.client.ConnectionPool", FakeConnectionPool):
        with patch("libs.redis_client.client.redis.Redis", return_value=fake_redis_instance):
            from libs.redis_client.client import RedisClient

            client = RedisClient()
            pipe = client.pipeline(transaction=False)
            assert pipe.transaction is False

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest
from redis.exceptions import RedisError

from apps.web_console_ng.core.sparkline_service import SparklineDataService


class FakeRedis:
    def __init__(self) -> None:
        self.data: dict[str, dict[str, float]] = {}
        self.expirations: dict[str, int] = {}

    async def zadd(self, key: str, mapping: dict[str, float]) -> None:
        bucket = self.data.setdefault(key, {})
        for member, score in mapping.items():
            bucket[member] = float(score)

    async def zrange(self, key: str, start: int, end: int) -> list[str]:
        bucket = self.data.get(key, {})
        items = sorted(bucket.items(), key=lambda item: item[1])
        members = [member for member, _ in items]
        length = len(members)
        if length == 0:
            return []

        def normalize(idx: int) -> int:
            return idx if idx >= 0 else length + idx

        start_idx = normalize(start)
        end_idx = normalize(end)
        if start_idx < 0:
            start_idx = 0
        if end_idx < 0:
            end_idx = 0
        if start_idx >= length:
            return []
        end_idx = min(end_idx, length - 1)
        if start_idx > end_idx:
            return []
        return members[start_idx : end_idx + 1]

    async def zremrangebyrank(self, key: str, start: int, end: int) -> None:
        members = await self.zrange(key, 0, -1)
        length = len(members)
        if length == 0:
            return

        def normalize(idx: int) -> int:
            return idx if idx >= 0 else length + idx

        start_idx = normalize(start)
        end_idx = normalize(end)
        if start_idx < 0:
            start_idx = 0
        if end_idx < 0:
            end_idx = 0
        if start_idx >= length:
            return
        end_idx = min(end_idx, length - 1)
        if start_idx > end_idx:
            return

        to_remove = set(members[start_idx : end_idx + 1])
        bucket = self.data.get(key, {})
        for member in to_remove:
            bucket.pop(member, None)

    async def zcard(self, key: str) -> int:
        return len(self.data.get(key, {}))

    async def expire(self, key: str, ttl: int) -> None:
        self.expirations[key] = ttl


class ErrorRedis(FakeRedis):
    async def zrange(self, key: str, start: int, end: int) -> list[str]:
        raise RedisError("down")

    async def zadd(self, key: str, mapping: dict[str, float]) -> None:
        raise RedisError("down")


@pytest.mark.asyncio()
async def test_record_point_rate_limit() -> None:
    fake = FakeRedis()
    t = 120

    def time_fn() -> int:
        return t

    service = SparklineDataService(fake, time_fn=time_fn, sample_interval_seconds=60)
    await service.record_point("user1", "AAPL", 1.0)
    await service.record_point("user1", "AAPL", 2.0)

    data = await service.get_sparkline_data("user1", "AAPL")
    assert data == [1.0]


@pytest.mark.asyncio()
async def test_record_point_trims_oldest() -> None:
    fake = FakeRedis()
    t = 0

    def time_fn() -> int:
        return t

    service = SparklineDataService(fake, time_fn=time_fn, max_points=3, sample_interval_seconds=60)

    for i in range(5):
        t = i * 60
        await service.record_point("user1", "AAPL", float(i))

    data = await service.get_sparkline_data("user1", "AAPL")
    assert data == [2.0, 3.0, 4.0]


@pytest.mark.asyncio()
async def test_parse_members_handles_bad_values() -> None:
    members: list[Any] = ["10:1.2", "11:bad", "12:3.4"]
    assert SparklineDataService._parse_members(members) == [1.2, 3.4]


@pytest.mark.asyncio()
async def test_record_positions_skips_invalids() -> None:
    fake = FakeRedis()
    service = SparklineDataService(fake, time_fn=lambda: 0)
    positions = [
        {"symbol": "AAPL", "unrealized_pl": 1.2},
        {"symbol": "", "unrealized_pl": 2.0},
        {"symbol": "MSFT", "unrealized_pl": None},
        {"symbol": "NVDA", "unrealized_pl": "bad"},
    ]
    await service.record_positions("user1", positions)
    data = await service.get_sparkline_data("user1", "AAPL")
    assert data == [1.2]


@pytest.mark.asyncio()
async def test_record_point_skips_nan_inf() -> None:
    fake = FakeRedis()
    service = SparklineDataService(fake, time_fn=lambda: 0)
    await service.record_point("user1", "AAPL", float("nan"))
    await service.record_point("user1", "AAPL", float("inf"))
    assert await service.get_sparkline_data("user1", "AAPL") == []


@pytest.mark.asyncio()
async def test_record_point_handles_redis_error() -> None:
    service = SparklineDataService(ErrorRedis(), time_fn=lambda: 0)
    await service.record_point("user1", "AAPL", 1.0)


@pytest.mark.asyncio()
async def test_get_sparkline_data_handles_redis_error() -> None:
    service = SparklineDataService(ErrorRedis(), time_fn=lambda: 0)
    data = await service.get_sparkline_data("user1", "AAPL")
    assert data == []


@pytest.mark.asyncio()
async def test_get_sparkline_map_handles_exceptions(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeRedis()
    service = SparklineDataService(fake, time_fn=lambda: 0)
    monkeypatch.setattr(service, "get_sparkline_data", AsyncMock(side_effect=RuntimeError("x")))
    result = await service.get_sparkline_map("user1", ["AAPL"])
    assert result == {"AAPL": []}


def test_parse_members_handles_bytes_and_nan() -> None:
    members: list[Any] = [b"10:1.2", b"11:nan", b"12:3.4"]
    assert SparklineDataService._parse_members(members) == [1.2, 3.4]


def test_prune_rate_limit_cache() -> None:
    fake = FakeRedis()
    service = SparklineDataService(fake, time_fn=lambda: 0, max_cache_entries=4)
    service._last_sampled = {
        ("u1", "A"): 0,
        ("u1", "B"): 0,
        ("u1", "C"): 0,
        ("u1", "D"): 0,
        ("u1", "E"): 0,
    }
    service._maybe_prune_rate_limit_cache(100)
    assert len(service._last_sampled) <= service._max_cache_entries

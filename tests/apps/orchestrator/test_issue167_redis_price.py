"""
Tests for Issue #167: _get_current_price Redis fallback.

Verifies that the orchestrator fetches current prices from the Redis
market data cache when the in-memory price_cache is empty.
"""

import json
from decimal import Decimal
from unittest.mock import MagicMock

import pytest

from apps.orchestrator.orchestrator import PriceUnavailableError, TradingOrchestrator


def _make_orchestrator(
    redis_client: MagicMock | None = None,
    price_cache: dict[str, Decimal] | None = None,
) -> TradingOrchestrator:
    return TradingOrchestrator(
        signal_service_url="http://localhost:8001",
        execution_gateway_url="http://localhost:8002",
        capital=Decimal("100000"),
        max_position_size=Decimal("10000"),
        price_cache=price_cache,
        redis_client=redis_client,
    )


class TestGetCurrentPriceRedis:
    """Test _get_current_price fetches from Redis when in-memory cache misses."""

    @pytest.mark.asyncio
    async def test_price_from_redis(self) -> None:
        """Price fetched from Redis when not in in-memory cache."""
        redis = MagicMock()
        redis.get.return_value = json.dumps(
            {"symbol": "AAPL", "bid": "149.00", "ask": "151.00", "mid": "150.00",
             "bid_size": 100, "ask_size": 100, "timestamp": "2026-04-02T12:00:00Z"}
        )
        orch = _make_orchestrator(redis_client=redis)

        price = await orch._get_current_price("AAPL")

        assert price == Decimal("150.00")
        redis.get.assert_called_once_with("price:AAPL")

    @pytest.mark.asyncio
    async def test_price_cached_locally_after_redis_fetch(self) -> None:
        """Price fetched from Redis is cached in-memory for the same run."""
        redis = MagicMock()
        redis.get.return_value = json.dumps(
            {"symbol": "AAPL", "bid": "149.00", "ask": "151.00", "mid": "150.00",
             "bid_size": 100, "ask_size": 100, "timestamp": "2026-04-02T12:00:00Z"}
        )
        orch = _make_orchestrator(redis_client=redis)

        await orch._get_current_price("AAPL")
        # Second call should use in-memory cache, not Redis
        price2 = await orch._get_current_price("AAPL")

        assert price2 == Decimal("150.00")
        assert redis.get.call_count == 1  # Only one Redis lookup

    @pytest.mark.asyncio
    async def test_in_memory_cache_takes_priority(self) -> None:
        """In-memory cache is checked before Redis."""
        redis = MagicMock()
        orch = _make_orchestrator(
            redis_client=redis,
            price_cache={"AAPL": Decimal("155.00")},
        )

        price = await orch._get_current_price("AAPL")

        assert price == Decimal("155.00")
        redis.get.assert_not_called()

    @pytest.mark.asyncio
    async def test_redis_miss_raises_price_unavailable(self) -> None:
        """PriceUnavailableError raised when Redis has no data."""
        redis = MagicMock()
        redis.get.return_value = None
        orch = _make_orchestrator(redis_client=redis)

        with pytest.raises(PriceUnavailableError) as exc_info:
            await orch._get_current_price("AAPL")
        assert exc_info.value.symbol == "AAPL"

    @pytest.mark.asyncio
    async def test_no_redis_client_raises_price_unavailable(self) -> None:
        """PriceUnavailableError raised when no Redis client provided."""
        orch = _make_orchestrator(redis_client=None)

        with pytest.raises(PriceUnavailableError):
            await orch._get_current_price("AAPL")

    @pytest.mark.asyncio
    async def test_redis_error_raises_price_unavailable(self) -> None:
        """PriceUnavailableError raised when Redis throws an exception."""
        redis = MagicMock()
        redis.get.side_effect = ConnectionError("Redis down")
        orch = _make_orchestrator(redis_client=redis)

        with pytest.raises(PriceUnavailableError):
            await orch._get_current_price("AAPL")

    @pytest.mark.asyncio
    async def test_zero_mid_price_treated_as_unavailable(self) -> None:
        """Zero mid price from Redis is treated as unavailable."""
        redis = MagicMock()
        redis.get.return_value = json.dumps(
            {"symbol": "AAPL", "bid": "0", "ask": "0", "mid": "0",
             "bid_size": 0, "ask_size": 0, "timestamp": "2026-04-02T12:00:00Z"}
        )
        orch = _make_orchestrator(redis_client=redis)

        with pytest.raises(PriceUnavailableError):
            await orch._get_current_price("AAPL")

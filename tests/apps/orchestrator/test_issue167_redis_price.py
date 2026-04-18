"""
Tests for Issue #167: _get_current_price Redis fallback.

Verifies that the orchestrator fetches current prices from the Redis
market data cache when the in-memory price_cache is empty.
"""

import asyncio
import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest

from apps.orchestrator.orchestrator import PriceUnavailableError, TradingOrchestrator


def _make_orchestrator(
    redis_client: MagicMock | None = None,
    price_cache: dict[str, Decimal] | None = None,
    max_price_age_seconds: int = 30,
) -> TradingOrchestrator:
    return TradingOrchestrator(
        signal_service_url="http://localhost:8001",
        execution_gateway_url="http://localhost:8002",
        capital=Decimal("100000"),
        max_position_size=Decimal("10000"),
        price_cache=price_cache,
        redis_client=redis_client,
        max_price_age_seconds=max_price_age_seconds,
    )


def _fresh_price_json(
    symbol: str = "AAPL",
    mid: str = "150.00",
    bid: str = "149.00",
    ask: str = "151.00",
    timestamp: str | None = None,
) -> str:
    """Build a valid Redis price JSON payload with a fresh timestamp."""
    if timestamp is None:
        timestamp = datetime.now(UTC).isoformat()
    return json.dumps(
        {
            "symbol": symbol,
            "bid": bid,
            "ask": ask,
            "mid": mid,
            "bid_size": 100,
            "ask_size": 100,
            "timestamp": timestamp,
        }
    )


class TestGetCurrentPriceRedis:
    """Test _get_current_price fetches from Redis when in-memory cache misses."""

    @pytest.mark.asyncio()
    async def test_price_from_redis(self) -> None:
        """Price fetched from Redis when not in in-memory cache."""
        redis = MagicMock()
        redis.get.return_value = _fresh_price_json()
        orch = _make_orchestrator(redis_client=redis)

        price = await orch._get_current_price("AAPL")

        assert price == Decimal("150.00")
        redis.get.assert_called_once_with("price:AAPL")

    @pytest.mark.asyncio()
    async def test_price_cached_locally_after_redis_fetch(self) -> None:
        """Price fetched from Redis is cached in-memory for the same run."""
        redis = MagicMock()
        redis.get.return_value = _fresh_price_json()
        orch = _make_orchestrator(redis_client=redis)

        await orch._get_current_price("AAPL")
        # Second call should use in-memory cache, not Redis
        price2 = await orch._get_current_price("AAPL")

        assert price2 == Decimal("150.00")
        assert redis.get.call_count == 1  # Only one Redis lookup

    @pytest.mark.asyncio()
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

    @pytest.mark.asyncio()
    async def test_redis_miss_raises_price_unavailable(self) -> None:
        """PriceUnavailableError raised when Redis has no data."""
        redis = MagicMock()
        redis.get.return_value = None
        orch = _make_orchestrator(redis_client=redis)

        with pytest.raises(PriceUnavailableError) as exc_info:
            await orch._get_current_price("AAPL")
        assert exc_info.value.symbol == "AAPL"

    @pytest.mark.asyncio()
    async def test_no_redis_client_raises_price_unavailable(self) -> None:
        """PriceUnavailableError raised when no Redis client provided."""
        orch = _make_orchestrator(redis_client=None)

        with pytest.raises(PriceUnavailableError):
            await orch._get_current_price("AAPL")

    @pytest.mark.asyncio()
    async def test_redis_error_raises_price_unavailable(self) -> None:
        """PriceUnavailableError raised when Redis throws an exception."""
        redis = MagicMock()
        redis.get.side_effect = ConnectionError("Redis down")
        orch = _make_orchestrator(redis_client=redis)

        with pytest.raises(PriceUnavailableError):
            await orch._get_current_price("AAPL")

        # Individual GET failure should also mark Redis unavailable
        # to prevent retry amplification on subsequent symbols
        assert orch._redis_unavailable is True

    @pytest.mark.asyncio()
    async def test_zero_mid_price_treated_as_unavailable(self) -> None:
        """Zero mid price from Redis is treated as unavailable."""
        redis = MagicMock()
        redis.get.return_value = _fresh_price_json(mid="0")
        orch = _make_orchestrator(redis_client=redis)

        with pytest.raises(PriceUnavailableError):
            await orch._get_current_price("AAPL")


class TestGetCurrentPriceEdgeCases:
    """Edge-case tests for Redis price parsing in _get_current_price."""

    @pytest.mark.asyncio()
    async def test_malformed_json_raises_price_unavailable(self) -> None:
        """PriceUnavailableError when Redis returns invalid JSON."""
        redis = MagicMock()
        redis.get.return_value = "not-valid-json{{"
        orch = _make_orchestrator(redis_client=redis)

        with pytest.raises(PriceUnavailableError):
            await orch._get_current_price("AAPL")

    @pytest.mark.asyncio()
    async def test_missing_mid_key_raises_price_unavailable(self) -> None:
        """PriceUnavailableError when Redis JSON has no 'mid' field."""
        redis = MagicMock()
        redis.get.return_value = json.dumps(
            {
                "symbol": "AAPL",
                "bid": "149.00",
                "ask": "151.00",
                "timestamp": datetime.now(UTC).isoformat(),
            }
        )
        orch = _make_orchestrator(redis_client=redis)

        with pytest.raises(PriceUnavailableError):
            await orch._get_current_price("AAPL")

    @pytest.mark.asyncio()
    async def test_null_mid_treated_as_unavailable(self) -> None:
        """Null mid price from Redis is treated as unavailable."""
        redis = MagicMock()
        redis.get.return_value = json.dumps(
            {
                "symbol": "AAPL",
                "bid": "149.00",
                "ask": "151.00",
                "mid": None,
                "timestamp": datetime.now(UTC).isoformat(),
            }
        )
        orch = _make_orchestrator(redis_client=redis)

        with pytest.raises(PriceUnavailableError):
            await orch._get_current_price("AAPL")

    @pytest.mark.asyncio()
    async def test_negative_mid_treated_as_unavailable(self) -> None:
        """Negative mid price from Redis is treated as unavailable."""
        redis = MagicMock()
        redis.get.return_value = _fresh_price_json(mid="-10.00")
        orch = _make_orchestrator(redis_client=redis)

        with pytest.raises(PriceUnavailableError):
            await orch._get_current_price("AAPL")

    @pytest.mark.asyncio()
    async def test_nan_mid_raises_price_unavailable(self) -> None:
        """NaN mid price from Redis is treated as unavailable."""
        redis = MagicMock()
        redis.get.return_value = _fresh_price_json(mid="NaN")
        orch = _make_orchestrator(redis_client=redis)

        with pytest.raises(PriceUnavailableError):
            await orch._get_current_price("AAPL")

    @pytest.mark.asyncio()
    async def test_infinity_mid_raises_price_unavailable(self) -> None:
        """Infinity mid price from Redis is treated as unavailable."""
        redis = MagicMock()
        redis.get.return_value = _fresh_price_json(mid="Infinity")
        orch = _make_orchestrator(redis_client=redis)

        with pytest.raises(PriceUnavailableError):
            await orch._get_current_price("AAPL")

    @pytest.mark.asyncio()
    async def test_stale_price_treated_as_unavailable(self) -> None:
        """Price older than MAX_PRICE_AGE_SECONDS is treated as stale."""
        stale_ts = (datetime.now(UTC) - timedelta(seconds=60)).isoformat()
        redis = MagicMock()
        redis.get.return_value = _fresh_price_json(timestamp=stale_ts)
        orch = _make_orchestrator(redis_client=redis)

        with pytest.raises(PriceUnavailableError):
            await orch._get_current_price("AAPL")

    @pytest.mark.asyncio()
    async def test_fresh_price_within_max_age_accepted(self) -> None:
        """Price within MAX_PRICE_AGE_SECONDS is accepted."""
        fresh_ts = (datetime.now(UTC) - timedelta(seconds=5)).isoformat()
        redis = MagicMock()
        redis.get.return_value = _fresh_price_json(timestamp=fresh_ts)
        orch = _make_orchestrator(redis_client=redis)

        price = await orch._get_current_price("AAPL")
        assert price == Decimal("150.00")

    @pytest.mark.asyncio()
    async def test_missing_timestamp_treated_as_unavailable(self) -> None:
        """Price data without timestamp field is treated as unavailable."""
        redis = MagicMock()
        redis.get.return_value = json.dumps(
            {
                "symbol": "AAPL",
                "bid": "149.00",
                "ask": "151.00",
                "mid": "150.00",
            }
        )
        orch = _make_orchestrator(redis_client=redis)

        with pytest.raises(PriceUnavailableError):
            await orch._get_current_price("AAPL")

    @pytest.mark.asyncio()
    async def test_redis_get_uses_asyncio_to_thread(self) -> None:
        """Verify Redis GET is wrapped in asyncio.to_thread to avoid blocking."""
        redis = MagicMock()
        redis.get.return_value = _fresh_price_json()
        orch = _make_orchestrator(redis_client=redis)

        with patch(
            "apps.orchestrator.orchestrator.asyncio.to_thread", wraps=asyncio.to_thread
        ) as mock_to_thread:
            await orch._get_current_price("AAPL")
            mock_to_thread.assert_called_once()


class TestCacheFreshnessRevalidation:
    """Tests for in-memory cache freshness revalidation on lookup."""

    @pytest.mark.asyncio()
    async def test_cached_price_evicted_when_stale(self) -> None:
        """In-memory cached price is evicted and re-fetched when it ages out."""
        redis = MagicMock()
        redis.get.return_value = _fresh_price_json(mid="160.00")
        orch = _make_orchestrator(redis_client=redis, max_price_age_seconds=10)

        # Seed cache with a price that has an old timestamp
        stale_ts = datetime.now(UTC) - timedelta(seconds=15)
        orch.price_cache["AAPL"] = Decimal("150.00")
        orch._price_timestamps["AAPL"] = stale_ts

        # Should evict stale entry and fetch fresh price from Redis
        price = await orch._get_current_price("AAPL")
        assert price == Decimal("160.00")
        redis.get.assert_called_once_with("price:AAPL")

    @pytest.mark.asyncio()
    async def test_cached_price_served_when_fresh(self) -> None:
        """In-memory cached price is served without Redis call when still fresh."""
        redis = MagicMock()
        orch = _make_orchestrator(redis_client=redis, max_price_age_seconds=30)

        # Seed cache with a recent timestamp
        fresh_ts = datetime.now(UTC) - timedelta(seconds=5)
        orch.price_cache["AAPL"] = Decimal("150.00")
        orch._price_timestamps["AAPL"] = fresh_ts

        price = await orch._get_current_price("AAPL")
        assert price == Decimal("150.00")
        redis.get.assert_not_called()

    @pytest.mark.asyncio()
    async def test_custom_max_price_age_respected(self) -> None:
        """Custom max_price_age_seconds value is used for staleness check."""
        redis = MagicMock()
        redis.get.return_value = _fresh_price_json(mid="170.00")
        # Set a very short max age
        orch = _make_orchestrator(redis_client=redis, max_price_age_seconds=2)

        # Seed cache with a 5-second-old timestamp (stale for 2s threshold)
        ts = datetime.now(UTC) - timedelta(seconds=5)
        orch.price_cache["AAPL"] = Decimal("150.00")
        orch._price_timestamps["AAPL"] = ts

        price = await orch._get_current_price("AAPL")
        assert price == Decimal("170.00")
        redis.get.assert_called_once()

    @pytest.mark.asyncio()
    async def test_redis_stale_with_custom_max_age(self) -> None:
        """Redis price that exceeds custom max_price_age_seconds is rejected."""
        stale_ts = (datetime.now(UTC) - timedelta(seconds=10)).isoformat()
        redis = MagicMock()
        redis.get.return_value = _fresh_price_json(timestamp=stale_ts)
        orch = _make_orchestrator(redis_client=redis, max_price_age_seconds=5)

        with pytest.raises(PriceUnavailableError):
            await orch._get_current_price("AAPL")


class TestBatchPrefetch:
    """Tests for _prefetch_prices batch MGET."""

    @pytest.mark.asyncio()
    async def test_prefetch_populates_cache(self) -> None:
        """_prefetch_prices populates price_cache for all symbols."""
        redis = MagicMock()
        redis.mget.return_value = [
            _fresh_price_json(symbol="AAPL", mid="150.00"),
            _fresh_price_json(symbol="MSFT", mid="300.00"),
        ]
        orch = _make_orchestrator(redis_client=redis)

        await orch._prefetch_prices(["AAPL", "MSFT"])

        assert orch.price_cache["AAPL"] == Decimal("150.00")
        assert orch.price_cache["MSFT"] == Decimal("300.00")
        redis.mget.assert_called_once()

    @pytest.mark.asyncio()
    async def test_prefetch_skips_fresh_cached(self) -> None:
        """_prefetch_prices skips symbols already fresh in cache."""
        redis = MagicMock()
        redis.mget.return_value = [_fresh_price_json(symbol="MSFT", mid="300.00")]
        orch = _make_orchestrator(redis_client=redis, max_price_age_seconds=30)

        # Seed AAPL as fresh
        orch.price_cache["AAPL"] = Decimal("150.00")
        orch._price_timestamps["AAPL"] = datetime.now(UTC)

        await orch._prefetch_prices(["AAPL", "MSFT"])

        # Only MSFT should be fetched
        redis.mget.assert_called_once()
        keys_arg = redis.mget.call_args[0][0]
        assert len(keys_arg) == 1
        assert keys_arg[0] == "price:MSFT"

    @pytest.mark.asyncio()
    async def test_prefetch_no_redis_client_is_noop(self) -> None:
        """_prefetch_prices is a no-op when redis_client is None."""
        orch = _make_orchestrator(redis_client=None)
        await orch._prefetch_prices(["AAPL"])
        assert orch.price_cache == {}

    @pytest.mark.asyncio()
    async def test_prefetch_error_is_non_fatal(self) -> None:
        """_prefetch_prices catches errors and marks Redis unavailable."""
        redis = MagicMock()
        redis.mget.side_effect = ConnectionError("Redis down")
        redis.get.return_value = _fresh_price_json()
        orch = _make_orchestrator(redis_client=redis)

        # Should not raise
        await orch._prefetch_prices(["AAPL"])

        # Cache is empty and Redis is marked unavailable
        assert "AAPL" not in orch.price_cache
        assert orch._redis_unavailable is True

    @pytest.mark.asyncio()
    async def test_prefetch_failure_prevents_per_symbol_retry(self) -> None:
        """When batch MGET fails, per-symbol GET is skipped to avoid retry amplification."""
        redis = MagicMock()
        redis.mget.side_effect = ConnectionError("Redis down")
        orch = _make_orchestrator(redis_client=redis)

        await orch._prefetch_prices(["AAPL"])

        # Per-symbol GET should be skipped
        with pytest.raises(PriceUnavailableError):
            await orch._get_current_price("AAPL")
        redis.get.assert_not_called()


    @pytest.mark.asyncio()
    async def test_individual_get_failure_prevents_subsequent_retries(self) -> None:
        """When per-symbol GET fails, subsequent symbols skip Redis."""
        redis = MagicMock()
        redis.get.side_effect = ConnectionError("Redis down")
        orch = _make_orchestrator(redis_client=redis)

        # First call fails and marks Redis unavailable
        with pytest.raises(PriceUnavailableError):
            await orch._get_current_price("AAPL")
        assert orch._redis_unavailable is True
        assert redis.get.call_count == 1

        # Second call should skip Redis entirely
        with pytest.raises(PriceUnavailableError):
            await orch._get_current_price("MSFT")
        # get() should NOT have been called again
        assert redis.get.call_count == 1


class TestSymbolMismatchAndFutureTimestamp:
    """Tests for symbol validation and future timestamp rejection."""

    @pytest.mark.asyncio()
    async def test_symbol_mismatch_treated_as_unavailable(self) -> None:
        """Redis payload with wrong symbol is rejected."""
        redis = MagicMock()
        # Return MSFT data when AAPL is requested
        redis.get.return_value = _fresh_price_json(symbol="MSFT", mid="300.00")
        orch = _make_orchestrator(redis_client=redis)

        with pytest.raises(PriceUnavailableError):
            await orch._get_current_price("AAPL")

    @pytest.mark.asyncio()
    async def test_future_timestamp_treated_as_unavailable(self) -> None:
        """Redis price with future timestamp is rejected as suspicious."""
        future_ts = (datetime.now(UTC) + timedelta(minutes=5)).isoformat()
        redis = MagicMock()
        redis.get.return_value = _fresh_price_json(timestamp=future_ts)
        orch = _make_orchestrator(redis_client=redis)

        with pytest.raises(PriceUnavailableError):
            await orch._get_current_price("AAPL")

    @pytest.mark.asyncio()
    async def test_matching_symbol_accepted(self) -> None:
        """Redis payload with matching symbol is accepted."""
        redis = MagicMock()
        redis.get.return_value = _fresh_price_json(symbol="AAPL", mid="150.00")
        orch = _make_orchestrator(redis_client=redis)

        price = await orch._get_current_price("AAPL")
        assert price == Decimal("150.00")

    @pytest.mark.asyncio()
    async def test_non_dict_json_treated_as_unavailable(self) -> None:
        """Non-dict JSON payloads (arrays, strings) are treated as unavailable."""
        redis = MagicMock()
        # JSON array is valid JSON but not a dict
        redis.get.return_value = '[1, 2, 3]'
        orch = _make_orchestrator(redis_client=redis)

        with pytest.raises(PriceUnavailableError):
            await orch._get_current_price("AAPL")

    @pytest.mark.asyncio()
    async def test_json_string_payload_treated_as_unavailable(self) -> None:
        """JSON string payload (not a dict) is treated as unavailable."""
        redis = MagicMock()
        redis.get.return_value = '"just a string"'
        orch = _make_orchestrator(redis_client=redis)

        with pytest.raises(PriceUnavailableError):
            await orch._get_current_price("AAPL")


class TestConstructorValidation:
    """Tests for TradingOrchestrator constructor validation."""

    def test_zero_max_price_age_rejected(self) -> None:
        """max_price_age_seconds=0 raises ValueError."""
        with pytest.raises(ValueError, match="must be > 0"):
            _make_orchestrator(max_price_age_seconds=0)

    def test_negative_max_price_age_rejected(self) -> None:
        """Negative max_price_age_seconds raises ValueError."""
        with pytest.raises(ValueError, match="must be > 0"):
            _make_orchestrator(max_price_age_seconds=-10)

    def test_valid_max_price_age_accepted(self) -> None:
        """Positive max_price_age_seconds is accepted."""
        orch = _make_orchestrator(max_price_age_seconds=60)
        assert orch.max_price_age_seconds == 60

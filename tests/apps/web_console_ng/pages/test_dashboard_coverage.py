"""Comprehensive coverage tests for dashboard page.

Targets uncovered code paths in dashboard.py to reach 85%+ coverage.
Focus: MarketPriceCache, helper functions, and core dashboard logic.
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock

import httpx
import pytest

from apps.web_console_ng.pages import dashboard as dashboard_module

# === MarketPriceCache Tests ===


@pytest.fixture(autouse=True)
def reset_market_cache() -> None:
    """Reset MarketPriceCache before each test."""
    dashboard_module.MarketPriceCache._cache = {}
    dashboard_module.MarketPriceCache._in_flight = {}
    yield
    dashboard_module.MarketPriceCache._cache = {}
    dashboard_module.MarketPriceCache._in_flight = {}


@pytest.mark.asyncio()
async def test_market_price_cache_scope_key_generation() -> None:
    """Test MarketPriceCache._get_scope_key generates correct keys."""
    key1 = dashboard_module.MarketPriceCache._get_scope_key("admin", ["strat1", "strat2"])
    key2 = dashboard_module.MarketPriceCache._get_scope_key("admin", ["strat2", "strat1"])
    key3 = dashboard_module.MarketPriceCache._get_scope_key("operator", ["strat1"])
    key4 = dashboard_module.MarketPriceCache._get_scope_key(None, None)
    key5 = dashboard_module.MarketPriceCache._get_scope_key("admin", None)

    # Same strategies, different order should produce same key
    assert key1 == key2

    # Different role or strategies should produce different key
    assert key1 != key3

    # None handling
    assert key4 == ("unknown", frozenset())
    assert key5 == ("admin", frozenset())


@pytest.mark.asyncio()
async def test_market_price_cache_returns_copy(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test MarketPriceCache.get_prices returns a copy to prevent mutation."""
    client = AsyncMock()
    client.fetch_market_prices = AsyncMock(return_value=[{"symbol": "AAPL", "price": 150.0}])

    monkeypatch.setattr(dashboard_module.time, "time", lambda: 1000.0)

    prices1 = await dashboard_module.MarketPriceCache.get_prices(
        client, user_id="user1", role="admin", strategies=["strat1"]
    )
    prices2 = await dashboard_module.MarketPriceCache.get_prices(
        client, user_id="user1", role="admin", strategies=["strat1"]
    )

    # Should be separate copies
    prices1[0]["price"] = 999.0
    assert prices2[0]["price"] == 150.0


@pytest.mark.asyncio()
async def test_market_price_cache_ttl_within_threshold(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test MarketPriceCache returns cached data within TTL."""
    client = AsyncMock()
    client.fetch_market_prices = AsyncMock(return_value=[{"symbol": "AAPL", "price": 150.0}])

    monkeypatch.setattr(dashboard_module.time, "time", lambda: 1000.0)
    await dashboard_module.MarketPriceCache.get_prices(
        client, user_id="user1", role="admin", strategies=["strat1"]
    )
    assert client.fetch_market_prices.call_count == 1

    # Within TTL (4 seconds), should return cached data
    monkeypatch.setattr(dashboard_module.time, "time", lambda: 1001.0)
    await dashboard_module.MarketPriceCache.get_prices(
        client, user_id="user1", role="admin", strategies=["strat1"]
    )
    assert client.fetch_market_prices.call_count == 1  # No new fetch


@pytest.mark.asyncio()
async def test_market_price_cache_ttl_expiry(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test MarketPriceCache refetches after TTL expires."""
    client = AsyncMock()
    client.fetch_market_prices = AsyncMock(return_value=[{"symbol": "AAPL", "price": 150.0}])

    monkeypatch.setattr(dashboard_module.time, "time", lambda: 1000.0)
    await dashboard_module.MarketPriceCache.get_prices(
        client, user_id="user1", role="admin", strategies=["strat1"]
    )
    assert client.fetch_market_prices.call_count == 1

    # After TTL (4 seconds)
    monkeypatch.setattr(dashboard_module.time, "time", lambda: 1005.0)
    await dashboard_module.MarketPriceCache.get_prices(
        client, user_id="user1", role="admin", strategies=["strat1"]
    )
    assert client.fetch_market_prices.call_count == 2


@pytest.mark.asyncio()
async def test_market_price_cache_error_cooldown_period(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test MarketPriceCache respects error cooldown."""
    client = AsyncMock()
    client.fetch_market_prices = AsyncMock(side_effect=httpx.RequestError("Network error"))

    scope_key = dashboard_module.MarketPriceCache._get_scope_key("admin", ["strat1"])
    dashboard_module.MarketPriceCache._cache[scope_key] = {
        "prices": [{"symbol": "AAPL", "price": 150.0}],
        "last_fetch": 0.0,
        "last_error": 1000.0,
    }

    # Within cooldown (10 seconds)
    monkeypatch.setattr(dashboard_module.time, "time", lambda: 1005.0)
    prices = await dashboard_module.MarketPriceCache.get_prices(
        client, user_id="user1", role="admin", strategies=["strat1"]
    )

    # Should return cached data without attempting fetch
    assert prices == [{"symbol": "AAPL", "price": 150.0}]
    assert client.fetch_market_prices.call_count == 0


@pytest.mark.asyncio()
async def test_market_price_cache_error_cooldown_expired(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test MarketPriceCache retries after error cooldown expires."""
    client = AsyncMock()
    client.fetch_market_prices = AsyncMock(return_value=[{"symbol": "AAPL", "price": 160.0}])

    scope_key = dashboard_module.MarketPriceCache._get_scope_key("admin", ["strat1"])
    dashboard_module.MarketPriceCache._cache[scope_key] = {
        "prices": [{"symbol": "AAPL", "price": 150.0}],
        "last_fetch": 0.0,
        "last_error": 1000.0,
    }

    # After cooldown (10 seconds)
    monkeypatch.setattr(dashboard_module.time, "time", lambda: 1011.0)
    prices = await dashboard_module.MarketPriceCache.get_prices(
        client, user_id="user1", role="admin", strategies=["strat1"]
    )

    # Should attempt new fetch and get updated data
    assert prices == [{"symbol": "AAPL", "price": 160.0}]
    assert client.fetch_market_prices.call_count == 1


@pytest.mark.asyncio()
async def test_market_price_cache_handles_http_status_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test MarketPriceCache handles HTTPStatusError."""
    from unittest.mock import MagicMock

    client = AsyncMock()
    response = MagicMock()
    response.status_code = 500
    client.fetch_market_prices = AsyncMock(
        side_effect=httpx.HTTPStatusError("Server error", request=None, response=response)
    )

    monkeypatch.setattr(dashboard_module.time, "time", lambda: 1000.0)
    prices = await dashboard_module.MarketPriceCache.get_prices(
        client, user_id="user1", role="admin", strategies=["strat1"]
    )

    # Should return empty list and set error timestamp
    assert prices == []
    scope_key = dashboard_module.MarketPriceCache._get_scope_key("admin", ["strat1"])
    assert dashboard_module.MarketPriceCache._cache[scope_key]["last_error"] > 0


@pytest.mark.asyncio()
async def test_market_price_cache_handles_validation_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test MarketPriceCache handles validation errors."""
    client = AsyncMock()
    client.fetch_market_prices = AsyncMock(side_effect=ValueError("Invalid data"))

    monkeypatch.setattr(dashboard_module.time, "time", lambda: 1000.0)
    prices = await dashboard_module.MarketPriceCache.get_prices(
        client, user_id="user1", role="admin", strategies=["strat1"]
    )

    # Should return empty list and set error timestamp
    assert prices == []
    scope_key = dashboard_module.MarketPriceCache._get_scope_key("admin", ["strat1"])
    assert dashboard_module.MarketPriceCache._cache[scope_key]["last_error"] > 0


@pytest.mark.asyncio()
async def test_market_price_cache_handles_key_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test MarketPriceCache handles KeyError."""
    client = AsyncMock()
    client.fetch_market_prices = AsyncMock(side_effect=KeyError("missing_key"))

    monkeypatch.setattr(dashboard_module.time, "time", lambda: 1000.0)
    prices = await dashboard_module.MarketPriceCache.get_prices(
        client, user_id="user1", role="admin", strategies=["strat1"]
    )

    # Should return empty list and set error timestamp
    assert prices == []
    scope_key = dashboard_module.MarketPriceCache._get_scope_key("admin", ["strat1"])
    assert dashboard_module.MarketPriceCache._cache[scope_key]["last_error"] > 0


@pytest.mark.asyncio()
async def test_market_price_cache_deduplicates_inflight_requests(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test MarketPriceCache deduplicates concurrent requests."""
    fetch_count = 0

    async def slow_fetch(*args: Any, **kwargs: Any) -> list[dict[str, Any]]:
        nonlocal fetch_count
        fetch_count += 1
        await asyncio.sleep(0.05)
        return [{"symbol": "AAPL", "price": 150.0}]

    client = AsyncMock()
    client.fetch_market_prices = slow_fetch

    monkeypatch.setattr(dashboard_module.time, "time", lambda: 1000.0)

    # Concurrent requests
    results = await asyncio.gather(
        dashboard_module.MarketPriceCache.get_prices(
            client, user_id="user1", role="admin", strategies=["strat1"]
        ),
        dashboard_module.MarketPriceCache.get_prices(
            client, user_id="user1", role="admin", strategies=["strat1"]
        ),
        dashboard_module.MarketPriceCache.get_prices(
            client, user_id="user1", role="admin", strategies=["strat1"]
        ),
    )

    # Should only fetch once
    assert fetch_count == 1
    assert results[0] == results[1] == results[2]


@pytest.mark.asyncio()
async def test_market_price_cache_missing_fetch_method(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test MarketPriceCache handles missing fetch_market_prices method."""
    from unittest.mock import AsyncMock

    # Use AsyncMock to properly handle async context, but without fetch_market_prices
    client = AsyncMock(spec=[])  # Empty spec means no methods

    monkeypatch.setattr(dashboard_module.time, "time", lambda: 1000.0)
    prices = await dashboard_module.MarketPriceCache.get_prices(
        client, user_id="user1", role="admin", strategies=["strat1"]
    )

    # Should return empty list and log warning
    assert prices == []


@pytest.mark.asyncio()
async def test_market_price_cache_strategy_isolation(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test that different strategy scopes have separate caches."""
    client = AsyncMock()
    client.fetch_market_prices = AsyncMock(return_value=[{"symbol": "AAPL", "price": 150.0}])

    monkeypatch.setattr(dashboard_module.time, "time", lambda: 1000.0)

    # User 1 with strategy A
    await dashboard_module.MarketPriceCache.get_prices(
        client, user_id="user1", role="operator", strategies=["strategy_a"]
    )
    assert client.fetch_market_prices.call_count == 1

    # User 2 with strategy B should trigger new fetch (different scope)
    await dashboard_module.MarketPriceCache.get_prices(
        client, user_id="user2", role="operator", strategies=["strategy_b"]
    )
    assert client.fetch_market_prices.call_count == 2  # New fetch for different scope

    # User 3 with same strategy A should use cached data
    await dashboard_module.MarketPriceCache.get_prices(
        client, user_id="user3", role="operator", strategies=["strategy_a"]
    )
    assert client.fetch_market_prices.call_count == 2  # No new fetch, same scope as user1


@pytest.mark.asyncio()
async def test_market_price_cache_role_isolation(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test that different roles have separate caches."""
    client = AsyncMock()
    client.fetch_market_prices = AsyncMock(return_value=[{"symbol": "AAPL", "price": 150.0}])

    monkeypatch.setattr(dashboard_module.time, "time", lambda: 1000.0)

    # Admin with strategy A
    await dashboard_module.MarketPriceCache.get_prices(
        client, user_id="user1", role="admin", strategies=["strategy_a"]
    )
    assert client.fetch_market_prices.call_count == 1

    # Operator with same strategy should trigger new fetch (different role)
    await dashboard_module.MarketPriceCache.get_prices(
        client, user_id="user2", role="operator", strategies=["strategy_a"]
    )
    assert client.fetch_market_prices.call_count == 2


@pytest.mark.asyncio()
async def test_market_price_cache_returns_stale_on_request_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test MarketPriceCache returns stale data on request error after cooldown."""
    client = AsyncMock()
    # First call succeeds
    client.fetch_market_prices = AsyncMock(return_value=[{"symbol": "AAPL", "price": 150.0}])

    monkeypatch.setattr(dashboard_module.time, "time", lambda: 1000.0)
    prices1 = await dashboard_module.MarketPriceCache.get_prices(
        client, user_id="user1", role="admin", strategies=["strat1"]
    )
    assert prices1 == [{"symbol": "AAPL", "price": 150.0}]

    # Second call fails after TTL expiry but before cooldown
    client.fetch_market_prices = AsyncMock(side_effect=httpx.RequestError("Network error"))
    monkeypatch.setattr(dashboard_module.time, "time", lambda: 1005.0)
    prices2 = await dashboard_module.MarketPriceCache.get_prices(
        client, user_id="user1", role="admin", strategies=["strat1"]
    )

    # Should return stale cached data
    assert prices2 == [{"symbol": "AAPL", "price": 150.0}]


# === Helper Function Tests ===


def test_dispatch_trading_state_event_success() -> None:
    """Test dispatch_trading_state_event with successful dispatch."""
    # Should not raise, fire-and-forget
    update = {"killSwitchState": "ENGAGED"}
    dashboard_module.dispatch_trading_state_event("client-123", update)


def test_dispatch_trading_state_event_handles_json_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test dispatch_trading_state_event handles JSON serialization errors."""
    # Non-serializable object
    update = {"data": lambda: None}
    dashboard_module.dispatch_trading_state_event("client-123", update)
    # Should not raise


def test_scope_meta_formatting() -> None:
    """Test _scope_meta formats scope key correctly."""
    scope_key = ("admin", frozenset(["strat1", "strat2"]))
    meta = dashboard_module.MarketPriceCache._scope_meta(scope_key)

    assert meta["role"] == "admin"
    assert sorted(meta["strategies"]) == ["strat1", "strat2"]


def test_scope_meta_empty_strategies() -> None:
    """Test _scope_meta handles empty strategies."""
    scope_key = ("operator", frozenset())
    meta = dashboard_module.MarketPriceCache._scope_meta(scope_key)

    assert meta["role"] == "operator"
    assert meta["strategies"] == []

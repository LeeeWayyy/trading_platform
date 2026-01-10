from __future__ import annotations

import pytest

from apps.web_console_ng.pages import dashboard as dashboard_module


class DummyTradingClient:
    def __init__(self) -> None:
        self.calls = 0
        self.prices = [{"symbol": "AAPL", "price": 150.0}]

    async def fetch_market_prices(
        self,
        user_id: str,
        *,
        role: str | None = None,
        strategies: list[str] | None = None,
    ):
        self.calls += 1
        return list(self.prices)


@pytest.fixture(autouse=True)
def reset_cache():
    """Reset cache state before each test."""
    dashboard_module.MarketPriceCache._cache = {}
    dashboard_module.MarketPriceCache._in_flight = {}
    yield
    dashboard_module.MarketPriceCache._cache = {}
    dashboard_module.MarketPriceCache._in_flight = {}


@pytest.mark.asyncio()
async def test_market_price_cache_ttl(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test that cache returns cached data within TTL."""
    client = DummyTradingClient()
    strategies = ["alpha_baseline"]

    monkeypatch.setattr(dashboard_module.time, "time", lambda: 1000.0)
    first = await dashboard_module.MarketPriceCache.get_prices(
        client, user_id="user1", role="admin", strategies=strategies
    )
    assert client.calls == 1
    assert first == client.prices

    # Within TTL (4 seconds), should return cached data without fetching
    monkeypatch.setattr(dashboard_module.time, "time", lambda: 1001.0)
    second = await dashboard_module.MarketPriceCache.get_prices(
        client, user_id="user1", role="admin", strategies=strategies
    )
    assert client.calls == 1  # No new fetch
    assert second == client.prices


@pytest.mark.asyncio()
async def test_market_price_cache_error_cooldown(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test that cache returns stale data during error cooldown."""
    client = DummyTradingClient()
    strategies = ["alpha_baseline"]
    scope_key = ("admin", frozenset(strategies))

    # Pre-populate cache with stale data and error state
    dashboard_module.MarketPriceCache._cache[scope_key] = {
        "prices": [{"symbol": "AAPL", "price": 150.0}],
        "last_fetch": 0.0,
        "last_error": 1002.0,
    }

    # Time is within error cooldown (10 seconds)
    monkeypatch.setattr(dashboard_module.time, "time", lambda: 1005.0)
    prices = await dashboard_module.MarketPriceCache.get_prices(
        client, user_id="user1", role="admin", strategies=strategies
    )

    # Should return cached data without fetching
    assert prices == [{"symbol": "AAPL", "price": 150.0}]
    assert client.calls == 0


@pytest.mark.asyncio()
async def test_market_price_cache_strategy_isolation(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test that different strategy scopes have separate caches."""
    client = DummyTradingClient()

    monkeypatch.setattr(dashboard_module.time, "time", lambda: 1000.0)

    # User 1 with strategy A
    strategies_a = ["strategy_a"]
    await dashboard_module.MarketPriceCache.get_prices(
        client, user_id="user1", role="operator", strategies=strategies_a
    )
    assert client.calls == 1

    # User 2 with strategy B should trigger new fetch (different scope)
    strategies_b = ["strategy_b"]
    await dashboard_module.MarketPriceCache.get_prices(
        client, user_id="user2", role="operator", strategies=strategies_b
    )
    assert client.calls == 2  # New fetch for different scope

    # User 3 with same strategy A should use cached data
    await dashboard_module.MarketPriceCache.get_prices(
        client, user_id="user3", role="operator", strategies=strategies_a
    )
    assert client.calls == 2  # No new fetch, same scope as user1

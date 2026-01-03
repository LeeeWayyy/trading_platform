from __future__ import annotations

import pytest

from apps.web_console_ng.pages import dashboard as dashboard_module


class DummyTradingClient:
    def __init__(self) -> None:
        self.calls = 0
        self.prices = [{"symbol": "AAPL", "price": 150.0}]

    async def fetch_market_prices(self):
        self.calls += 1
        return list(self.prices)


@pytest.mark.asyncio()
async def test_market_price_cache_ttl(monkeypatch: pytest.MonkeyPatch) -> None:
    client = DummyTradingClient()

    dashboard_module.MarketPriceCache._prices = []
    dashboard_module.MarketPriceCache._last_fetch = 0.0
    dashboard_module.MarketPriceCache._last_error = 0.0

    monkeypatch.setattr(dashboard_module.time, "time", lambda: 1000.0)
    first = await dashboard_module.MarketPriceCache.get_prices(client)
    assert client.calls == 1
    assert first == client.prices

    monkeypatch.setattr(dashboard_module.time, "time", lambda: 1001.0)
    second = await dashboard_module.MarketPriceCache.get_prices(client)
    assert client.calls == 1
    assert second == client.prices


@pytest.mark.asyncio()
async def test_market_price_cache_error_cooldown(monkeypatch: pytest.MonkeyPatch) -> None:
    client = DummyTradingClient()

    dashboard_module.MarketPriceCache._prices = [{"symbol": "AAPL", "price": 150.0}]
    dashboard_module.MarketPriceCache._last_fetch = 0.0
    dashboard_module.MarketPriceCache._last_error = 1002.0

    monkeypatch.setattr(dashboard_module.time, "time", lambda: 1005.0)
    prices = await dashboard_module.MarketPriceCache.get_prices(client)

    assert prices == [{"symbol": "AAPL", "price": 150.0}]
    assert client.calls == 0

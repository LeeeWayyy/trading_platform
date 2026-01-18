from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any
from unittest.mock import MagicMock

import pytest

import libs.data.market_data.alpaca_stream as alpaca_stream
from libs.core.redis_client import RedisKeys
from libs.data.market_data.types import PriceUpdateEvent


class _FakeStockDataStream:
    def __init__(self, api_key: str, secret_key: str) -> None:
        self.api_key = api_key
        self.secret_key = secret_key
        self.subscriptions: list[tuple[object, tuple[str, ...]]] = []
        self.unsubscribed: list[str] = []
        self.run_called = 0
        self.stop_called = 0

    def subscribe_quotes(self, handler: object, *symbols: str) -> None:
        self.subscriptions.append((handler, symbols))

    def unsubscribe_quotes(self, symbol: str) -> None:
        self.unsubscribed.append(symbol)

    def run(self) -> None:
        self.run_called += 1

    def stop(self) -> None:
        self.stop_called += 1


@pytest.fixture()
def fake_stream_cls(monkeypatch: pytest.MonkeyPatch) -> type[_FakeStockDataStream]:
    monkeypatch.setattr(alpaca_stream, "StockDataStream", _FakeStockDataStream)
    return _FakeStockDataStream


@pytest.fixture()
def redis_client() -> MagicMock:
    return MagicMock()


@pytest.fixture()
def event_publisher() -> MagicMock:
    return MagicMock()


@pytest.fixture()
def stream(
    fake_stream_cls: type[_FakeStockDataStream],
    redis_client: MagicMock,
    event_publisher: MagicMock,
) -> alpaca_stream.AlpacaMarketDataStream:
    return alpaca_stream.AlpacaMarketDataStream(
        api_key="key",
        secret_key="secret",
        redis_client=redis_client,
        event_publisher=event_publisher,
    )


@pytest.mark.asyncio()
async def test_subscribe_and_unsubscribe_ref_counts(
    stream: alpaca_stream.AlpacaMarketDataStream,
) -> None:
    await stream.subscribe_symbols(["AAPL", "MSFT"], source="manual")
    await stream.subscribe_symbols(["AAPL"], source="position")

    assert stream.get_subscribed_symbols() == ["AAPL", "MSFT"]
    assert stream.get_subscription_sources() == {
        "AAPL": ["manual", "position"],
        "MSFT": ["manual"],
    }

    # Only the first call should have triggered Alpaca subscription
    fake_stream = stream.stream
    assert isinstance(fake_stream, _FakeStockDataStream)
    assert len(fake_stream.subscriptions) == 1
    _, symbols = fake_stream.subscriptions[0]
    assert set(symbols) == {"AAPL", "MSFT"}

    await stream.unsubscribe_symbols(["AAPL"], source="position")
    assert fake_stream.unsubscribed == []
    assert stream.get_subscription_sources()["AAPL"] == ["manual"]

    await stream.unsubscribe_symbols(["AAPL"], source="manual")
    assert fake_stream.unsubscribed == ["AAPL"]
    assert "AAPL" not in stream.get_subscription_sources()


@pytest.mark.asyncio()
async def test_handle_quote_mapping_writes_cache_and_event(
    stream: alpaca_stream.AlpacaMarketDataStream,
    redis_client: MagicMock,
    event_publisher: MagicMock,
) -> None:
    quote: dict[str, Any] = {
        "symbol": "AAPL",
        "bid_price": "100.00",
        "ask_price": "100.10",
        "bid_size": 10,
        "ask_size": 12,
        "timestamp": "2025-01-01T12:00:00Z",
        "ask_exchange": "NASDAQ",
    }

    await stream._handle_quote(quote)

    cache_key = RedisKeys.price("AAPL")
    redis_client.set.assert_called_once()
    args, kwargs = redis_client.set.call_args
    assert args[0] == cache_key
    assert kwargs.get("ttl") == stream.price_ttl

    event_publisher.publish.assert_called_once()
    channel, event = event_publisher.publish.call_args.args
    assert channel == "price.updated.AAPL"
    assert isinstance(event, PriceUpdateEvent)
    assert event.symbol == "AAPL"
    assert event.price == Decimal("100.05")


@pytest.mark.asyncio()
async def test_handle_quote_missing_timestamp_no_side_effects(
    stream: alpaca_stream.AlpacaMarketDataStream,
    redis_client: MagicMock,
    event_publisher: MagicMock,
) -> None:
    quote: dict[str, Any] = {
        "symbol": "AAPL",
        "bid_price": "100.00",
        "ask_price": "100.10",
        "timestamp": None,
    }

    await stream._handle_quote(quote)

    redis_client.set.assert_not_called()
    event_publisher.publish.assert_not_called()


@pytest.mark.asyncio()
async def test_handle_quote_invalid_timestamp_type_no_side_effects(
    stream: alpaca_stream.AlpacaMarketDataStream,
    redis_client: MagicMock,
    event_publisher: MagicMock,
) -> None:
    quote: dict[str, Any] = {
        "symbol": "AAPL",
        "bid_price": "100.00",
        "ask_price": "100.10",
        "timestamp": 123456,
    }

    await stream._handle_quote(quote)

    redis_client.set.assert_not_called()
    event_publisher.publish.assert_not_called()


@pytest.mark.asyncio()
async def test_handle_quote_validation_error_is_swallowed(
    stream: alpaca_stream.AlpacaMarketDataStream,
    redis_client: MagicMock,
    event_publisher: MagicMock,
) -> None:
    quote: dict[str, Any] = {
        "symbol": "AAPL",
        "bid_price": "101.00",
        "ask_price": "100.00",
        "timestamp": datetime(2025, 1, 1, 12, 0, tzinfo=timezone.utc),
    }

    await stream._handle_quote(quote)

    redis_client.set.assert_not_called()
    event_publisher.publish.assert_not_called()

"""
Tests for ADV endpoint in Market Data Service.
"""

from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from libs.data.market_data.exceptions import MarketDataError


@pytest.fixture()
def test_client(monkeypatch):
    """Create FastAPI test client with mocked lifespan to avoid external dependencies."""
    monkeypatch.setenv("ALPACA_API_KEY", "test_key")
    monkeypatch.setenv("ALPACA_SECRET_KEY", "test_secret")
    monkeypatch.setenv("REDIS_HOST", "localhost")
    monkeypatch.setenv("REDIS_PORT", "6379")
    monkeypatch.setenv("EXECUTION_GATEWAY_URL", "http://localhost:8002")
    monkeypatch.setenv("API_AUTH_MODE", "log_only")
    monkeypatch.setenv("RATE_LIMIT_MODE", "log_only")

    @asynccontextmanager
    async def mock_lifespan(app):
        yield

    with patch("apps.market_data_service.main.lifespan", mock_lifespan):
        from apps.market_data_service.main import app

        return TestClient(app, raise_server_exceptions=False)


class DummyCache:
    def __init__(self):
        self.store: dict[str, str] = {}

    def get(self, key: str) -> str | None:
        return self.store.get(key)

    def set(self, key: str, value: str, ex: int | None = None) -> None:
        self.store[key] = value


class DummyProvider:
    def __init__(self, adv_data=None, bars_data=None, quote_data=None, error=None):
        self._adv_data = adv_data
        self._bars_data = bars_data or []
        self._quote_data = quote_data
        self._error = error

    async def get_adv(self, symbol: str):
        if self._error:
            raise self._error
        return self._adv_data

    async def get_bars(self, symbol: str, *, timeframe: str = "5Min", limit: int = 240):
        if isinstance(self._error, ValueError):
            raise self._error
        if self._error:
            raise self._error
        return list(self._bars_data)

    async def get_latest_quote(self, symbol: str):
        if self._error:
            raise self._error
        return self._quote_data


def test_adv_returns_404_for_unknown_symbol(test_client, monkeypatch):
    from apps.market_data_service.routes import market_data

    monkeypatch.setattr(market_data, "_get_provider", lambda: DummyProvider(adv_data=None))
    monkeypatch.setattr(market_data, "_get_cache", lambda: None)

    response = test_client.get("/api/v1/market-data/INVALID123/adv")
    assert response.status_code == 404


def test_adv_returns_404_for_otc_symbol(test_client, monkeypatch):
    from apps.market_data_service.routes import market_data

    monkeypatch.setattr(market_data, "_get_provider", lambda: DummyProvider(adv_data=None))
    monkeypatch.setattr(market_data, "_get_cache", lambda: None)

    response = test_client.get("/api/v1/market-data/TSNPD/adv")
    assert response.status_code == 404


def test_adv_returns_503_when_provider_unavailable(test_client, monkeypatch):
    from apps.market_data_service.routes import market_data

    monkeypatch.setattr(
        market_data, "_get_provider", lambda: DummyProvider(error=MarketDataError("down"))
    )
    monkeypatch.setattr(market_data, "_get_cache", lambda: None)

    response = test_client.get("/api/v1/market-data/AAPL/adv")
    assert response.status_code == 503


def test_adv_returns_cached_value_when_provider_temporarily_unavailable(test_client, monkeypatch):
    from apps.market_data_service.routes import market_data

    cache = DummyCache()
    cached_at = datetime.now(UTC) - timedelta(hours=2)
    cache.set(
        "adv:AAPL",
        (
            "{"
            f'"adv": 1000, "data_date": "{(datetime.now(UTC).date()).isoformat()}", '
            f'"source": "alpaca", "cached_at": "{cached_at.isoformat()}"'
            "}"
        ),
    )

    monkeypatch.setattr(
        market_data, "_get_provider", lambda: DummyProvider(error=MarketDataError("down"))
    )
    monkeypatch.setattr(market_data, "_get_cache", lambda: cache)

    response = test_client.get("/api/v1/market-data/AAPL/adv")
    assert response.status_code == 200
    body = response.json()
    assert body["cached"] is True
    assert body["adv"] == 1000


def test_adv_staleness_calculation(test_client, monkeypatch):
    from apps.market_data_service.routes import market_data

    cache = DummyCache()
    cached_at = datetime.now(UTC) - timedelta(hours=2)
    cache.set(
        "adv:AAPL",
        (
            "{"
            f'"adv": 1000, "data_date": "2025-01-01", '
            f'"source": "alpaca", "cached_at": "{cached_at.isoformat()}"'
            "}"
        ),
    )

    monkeypatch.setattr(market_data, "_trading_days_since", lambda *_: 6)
    monkeypatch.setattr(
        market_data, "_get_provider", lambda: DummyProvider(error=MarketDataError("down"))
    )
    monkeypatch.setattr(market_data, "_get_cache", lambda: cache)

    response = test_client.get("/api/v1/market-data/AAPL/adv")
    assert response.status_code == 200
    body = response.json()
    assert body["stale"] is True


def test_bars_returns_historical_payload(test_client, monkeypatch):
    from apps.market_data_service.routes import market_data

    expected_bars = [
        {
            "timestamp": "2026-04-20T13:30:00+00:00",
            "open": 180.1,
            "high": 181.0,
            "low": 179.9,
            "close": 180.6,
            "volume": 1200,
        }
    ]
    monkeypatch.setattr(
        market_data,
        "_get_provider",
        lambda: DummyProvider(bars_data=expected_bars),
    )

    response = test_client.get("/api/v1/market-data/aapl/bars?timeframe=5Min&limit=100")
    assert response.status_code == 200
    body = response.json()
    assert body["symbol"] == "AAPL"
    assert body["timeframe"] == "5Min"
    assert len(body["bars"]) == 1
    assert body["bars"][0]["open"] == expected_bars[0]["open"]
    assert body["bars"][0]["high"] == expected_bars[0]["high"]
    assert body["bars"][0]["low"] == expected_bars[0]["low"]
    assert body["bars"][0]["close"] == expected_bars[0]["close"]
    assert body["bars"][0]["volume"] == expected_bars[0]["volume"]
    assert body["bars"][0]["timestamp"] in {
        "2026-04-20T13:30:00Z",
        "2026-04-20T13:30:00+00:00",
    }


def test_latest_quote_returns_top_of_book_payload(test_client, monkeypatch):
    from apps.market_data_service.routes import market_data

    monkeypatch.setattr(
        market_data,
        "_get_provider",
        lambda: DummyProvider(
            quote_data={
                "symbol": "AAPL",
                "bid_price": Decimal("180.1"),
                "ask_price": Decimal("180.2"),
                "bid_size": 100,
                "ask_size": 200,
                "timestamp": "2026-04-20T13:30:01+00:00",
            }
        ),
    )

    response = test_client.get("/api/v1/market-data/aapl/latest-quote")
    assert response.status_code == 200
    body = response.json()
    assert body["symbol"] == "AAPL"
    assert body["bid_price"] == "180.1"
    assert body["ask_price"] == "180.2"
    assert body["bid_size"] == 100
    assert body["ask_size"] == 200


def test_latest_quote_returns_404_when_quote_missing(test_client, monkeypatch):
    from apps.market_data_service.routes import market_data

    monkeypatch.setattr(market_data, "_get_provider", lambda: DummyProvider(quote_data=None))

    response = test_client.get("/api/v1/market-data/AAPL/latest-quote")
    assert response.status_code == 404


def test_bars_returns_400_for_invalid_limit(test_client):
    response = test_client.get("/api/v1/market-data/AAPL/bars?limit=0")
    assert response.status_code == 400


def test_bars_returns_400_for_invalid_timeframe(test_client, monkeypatch):
    from apps.market_data_service.routes import market_data

    monkeypatch.setattr(
        market_data,
        "_get_provider",
        lambda: DummyProvider(error=ValueError("Unsupported timeframe: 2Min")),
    )

    response = test_client.get("/api/v1/market-data/AAPL/bars?timeframe=2Min")
    assert response.status_code == 400
    assert "Unsupported timeframe" in response.text


def test_bars_returns_503_when_provider_unavailable(test_client, monkeypatch):
    from apps.market_data_service.routes import market_data

    monkeypatch.setattr(
        market_data,
        "_get_provider",
        lambda: DummyProvider(error=MarketDataError("down")),
    )

    response = test_client.get("/api/v1/market-data/AAPL/bars")
    assert response.status_code == 503

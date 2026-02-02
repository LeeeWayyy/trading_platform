"""
Tests for ADV endpoint in Market Data Service.
"""

from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
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
    def __init__(self, adv_data=None, error=None):
        self._adv_data = adv_data
        self._error = error

    async def get_adv(self, symbol: str):
        if self._error:
            raise self._error
        return self._adv_data


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

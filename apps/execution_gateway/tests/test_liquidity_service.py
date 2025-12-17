"""Tests for LiquidityService ADV fetching and caching."""

from apps.execution_gateway.liquidity_service import LiquidityService


class DummyResponse:
    def __init__(self, status_code: int, payload: dict, text: str | None = None) -> None:
        self.status_code = status_code
        self._payload = payload
        self.text = text or ""

    def json(self):
        return self._payload


class DummyClient:
    def __init__(self, response: DummyResponse) -> None:
        self.response = response
        self.calls = 0

    def get(self, _url, params=None, headers=None):
        self.calls += 1
        return self.response


def test_get_adv_success_and_cache():
    response = DummyResponse(200, {"bars": [{"v": 100}, {"v": 200}, {"v": 300}]})
    client = DummyClient(response)
    service = LiquidityService(
        api_key="key",
        api_secret="secret",
        ttl_seconds=3600,
        http_client=client,
    )

    adv1 = service.get_adv("aapl")
    adv2 = service.get_adv("AAPL")

    assert adv1 == 200
    assert adv2 == 200
    assert client.calls == 1


def test_get_adv_missing_credentials_skips_request():
    response = DummyResponse(200, {"bars": [{"v": 100}]})
    client = DummyClient(response)
    service = LiquidityService(api_key="", api_secret="", http_client=client)

    assert service.get_adv("AAPL") is None
    assert client.calls == 0


def test_get_adv_non_200_response_returns_none():
    response = DummyResponse(500, {"error": "fail"}, text="fail")
    client = DummyClient(response)
    service = LiquidityService(api_key="key", api_secret="secret", http_client=client)

    assert service.get_adv("AAPL") is None
    assert client.calls == 1


def test_get_adv_empty_bars_returns_none():
    response = DummyResponse(200, {"bars": []})
    client = DummyClient(response)
    service = LiquidityService(api_key="key", api_secret="secret", http_client=client)

    assert service.get_adv("AAPL") is None
    assert client.calls == 1


def test_get_adv_volume_field_variant():
    response = DummyResponse(200, {"bars": [{"volume": 50}, {"volume": 150}]})
    client = DummyClient(response)
    service = LiquidityService(api_key="key", api_secret="secret", http_client=client)

    assert service.get_adv("AAPL") == 100
    assert client.calls == 1

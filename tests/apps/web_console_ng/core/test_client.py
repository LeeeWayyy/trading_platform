from __future__ import annotations

import hashlib
import hmac
import json
from typing import Any

import httpx
import pytest
import respx

from apps.web_console_ng import config
from apps.web_console_ng.core.client import AsyncTradingClient


@pytest.fixture(autouse=True)
def reset_trading_client() -> None:
    client = AsyncTradingClient.get()
    client._http_client = None


def test_get_auth_headers_debug_fallbacks(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "DEBUG", True)
    monkeypatch.setattr(config, "DEV_ROLE", "dev-role")
    monkeypatch.setattr(config, "DEV_STRATEGIES", ["strat-b", "strat-a"])
    monkeypatch.setattr(config, "DEV_USER_ID", "dev-user")
    monkeypatch.delenv("INTERNAL_TOKEN_SECRET", raising=False)

    client = AsyncTradingClient.get()
    headers = client._get_auth_headers(user_id="", role=None, strategies=None)

    assert headers["X-User-Role"] == "dev-role"
    assert headers["X-User-Id"] == "dev-user"
    assert headers["X-User-Strategies"] == "strat-a,strat-b"
    assert "X-User-Signature" not in headers


def test_get_auth_headers_production_requires_user_id(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "DEBUG", False)
    monkeypatch.setenv("INTERNAL_TOKEN_SECRET", "secret")

    client = AsyncTradingClient.get()
    with pytest.raises(ValueError, match="User ID required"):
        client._get_auth_headers(user_id="", role="admin", strategies=["s1"])


def test_get_auth_headers_production_requires_role(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "DEBUG", False)
    monkeypatch.setenv("INTERNAL_TOKEN_SECRET", "secret")

    client = AsyncTradingClient.get()
    with pytest.raises(ValueError, match="Role required"):
        client._get_auth_headers(user_id="user-1", role=None, strategies=["s1"])


def test_get_auth_headers_signature(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "DEBUG", True)
    monkeypatch.setenv("INTERNAL_TOKEN_SECRET", "secret")
    monkeypatch.setattr("time.time", lambda: 1700000000)

    client = AsyncTradingClient.get()
    headers = client._get_auth_headers(
        user_id="user-1", role="trader", strategies=["b", "a"]
    )

    payload_data = {
        "uid": "user-1",
        "role": "trader",
        "strats": "a,b",
        "ts": "1700000000",
    }
    payload = json.dumps(payload_data, separators=(",", ":"), sort_keys=True)
    expected_sig = hmac.new(
        b"secret", payload.encode("utf-8"), hashlib.sha256
    ).hexdigest()

    assert headers["X-User-Strategies"] == "a,b"
    assert headers["X-Request-Timestamp"] == "1700000000"
    assert headers["X-User-Signature"] == expected_sig


@pytest.mark.asyncio()
@respx.mock
async def test_fetch_kill_switch_status_maps_active(
    trading_client: AsyncTradingClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("INTERNAL_TOKEN_SECRET", raising=False)
    route = respx.get("http://testserver/api/v1/kill-switch/status").mock(
        return_value=httpx.Response(200, json={"state": "ACTIVE"})
    )

    result = await trading_client.fetch_kill_switch_status("user-1")

    assert result["state"] == "DISENGAGED"
    assert route.call_count == 1


def test_json_dict_requires_object() -> None:
    client = AsyncTradingClient.get()
    response = httpx.Response(200, json=["not", "a", "dict"])

    with pytest.raises(ValueError, match="Expected JSON object response"):
        client._json_dict(response)

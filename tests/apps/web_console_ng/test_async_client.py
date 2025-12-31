"""Tests for AsyncTradingClient retry policy and lifecycle."""

from __future__ import annotations

import httpx
import pytest
import respx
from httpx import Response

from apps.web_console_ng.core.client import AsyncTradingClient


@pytest.mark.asyncio()
@respx.mock
async def test_retry_transport_error_get(trading_client: AsyncTradingClient) -> None:
    route = respx.get("http://testserver/api/v1/positions").mock(
        side_effect=[
            httpx.ConnectError(
                "boom",
                request=httpx.Request("GET", "http://testserver/api/v1/positions"),
            ),
            Response(200, json={"ok": True}),
        ]
    )

    result = await trading_client.fetch_positions("user-1")

    assert result == {"ok": True}
    assert route.call_count == 2


@pytest.mark.asyncio()
@respx.mock
async def test_retry_transport_error_post(trading_client: AsyncTradingClient) -> None:
    route = respx.post("http://testserver/api/v1/kill-switch").mock(
        side_effect=[
            httpx.ConnectError(
                "boom",
                request=httpx.Request("POST", "http://testserver/api/v1/kill-switch"),
            ),
            Response(200, json={"status": "ok"}),
        ]
    )

    result = await trading_client.trigger_kill_switch("user-1")

    assert result == {"status": "ok"}
    assert route.call_count == 2


@pytest.mark.asyncio()
@respx.mock
async def test_retry_on_5xx_for_get_only(trading_client: AsyncTradingClient) -> None:
    route = respx.get("http://testserver/api/v1/positions").mock(
        side_effect=[
            Response(500, json={"error": "boom"}),
            Response(200, json={"ok": True}),
        ]
    )

    result = await trading_client.fetch_positions("user-1")

    assert result == {"ok": True}
    assert route.call_count == 2


@pytest.mark.asyncio()
@respx.mock
async def test_no_retry_on_5xx_for_post(trading_client: AsyncTradingClient) -> None:
    route = respx.post("http://testserver/api/v1/kill-switch").mock(
        return_value=Response(500, json={"error": "boom"})
    )

    with pytest.raises(httpx.HTTPStatusError):
        await trading_client.trigger_kill_switch("user-1")

    assert route.call_count == 1


@pytest.mark.asyncio()
@respx.mock
async def test_no_retry_on_4xx(trading_client: AsyncTradingClient) -> None:
    route = respx.get("http://testserver/api/v1/positions").mock(
        return_value=Response(404, json={"error": "not found"})
    )

    with pytest.raises(httpx.HTTPStatusError):
        await trading_client.fetch_positions("user-1")

    assert route.call_count == 1


@pytest.mark.asyncio()
async def test_runtime_error_before_startup() -> None:
    client = AsyncTradingClient.get()
    client._http_client = None

    with pytest.raises(RuntimeError):
        await client.fetch_positions("user-1")


@pytest.mark.asyncio()
async def test_client_lifecycle() -> None:
    client = AsyncTradingClient.get()
    client._http_client = None

    await client.startup()
    assert client._http_client is not None

    await client.shutdown()
    assert client._http_client is None

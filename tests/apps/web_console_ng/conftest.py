"""Shared fixtures for web_console_ng tests."""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest

from apps.web_console_ng import config
from apps.web_console_ng.core import retry
from apps.web_console_ng.core.client import AsyncTradingClient


@pytest.fixture(autouse=True)
def _no_retry_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _sleep(_: float) -> None:
        return None

    monkeypatch.setattr(retry.asyncio, "sleep", _sleep)


@pytest.fixture()
async def trading_client(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[AsyncTradingClient]:
    monkeypatch.setattr(config, "EXECUTION_GATEWAY_URL", "http://testserver")
    client = AsyncTradingClient.get()
    client._http_client = None
    await client.startup()
    try:
        yield client
    finally:
        await client.shutdown()

"""Tests for ConnectionMonitorRegistry."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from apps.web_console_ng import config
from apps.web_console_ng.core.connection_monitor import ConnectionMonitorRegistry


class _SessionStore:
    def __init__(self, session: dict | None) -> None:
        self._session = session

    async def validate_session(
        self, cookie_value: str, client_ip: str, user_agent: str | None = None
    ):
        return self._session


class _Client:
    def __init__(self, environ: dict[str, str]) -> None:
        self.id = "client-1"
        self.environ = environ
        self.state = SimpleNamespace()
        self.disconnect_called = False

    def disconnect(self) -> None:
        self.disconnect_called = True


@pytest.fixture(autouse=True)
def _reset_singleton() -> None:
    ConnectionMonitorRegistry._instance = None


@pytest.mark.asyncio()
async def test_hooks_registered_once(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = {"connect": 0, "disconnect": 0}

    def _on_connect(handler):
        calls["connect"] += 1
        return handler

    def _on_disconnect(handler):
        calls["disconnect"] += 1
        return handler

    from apps.web_console_ng.core import connection_monitor

    monkeypatch.setattr(connection_monitor.app, "on_connect", _on_connect)
    monkeypatch.setattr(connection_monitor.app, "on_disconnect", _on_disconnect)

    registry = ConnectionMonitorRegistry.get(session_store=_SessionStore({}))
    registry.register_hooks_once()
    registry.register_hooks_once()

    assert calls["connect"] == 1
    assert calls["disconnect"] == 1


@pytest.mark.asyncio()
async def test_origin_validation() -> None:
    registry = ConnectionMonitorRegistry.get(
        session_store=_SessionStore({"user": {"user_id": "u1"}}),
        allowed_hosts=["example.com"],
    )
    cookie_value = f"{config.SESSION_COOKIE_NAME}=cookie"
    client = _Client(
        {
            "HTTP_ORIGIN": "https://example.com",
            "HTTP_COOKIE": cookie_value,
            "REMOTE_ADDR": "10.0.0.1",
        }
    )

    await registry._handle_connect(client)
    assert client.disconnect_called is False
    assert client.state.user == {"user_id": "u1"}

    client_bad = _Client(
        {
            "HTTP_ORIGIN": "https://evil.com",
            "HTTP_COOKIE": cookie_value,
            "REMOTE_ADDR": "10.0.0.2",
        }
    )
    await registry._handle_connect(client_bad)
    assert client_bad.disconnect_called is True


@pytest.mark.asyncio()
async def test_session_validation_on_connect() -> None:
    registry = ConnectionMonitorRegistry.get(
        session_store=_SessionStore(None),
        allowed_hosts=["example.com"],
    )
    cookie_value = f"{config.SESSION_COOKIE_NAME}=cookie"
    client = _Client(
        {
            "HTTP_ORIGIN": "https://example.com",
            "HTTP_COOKIE": cookie_value,
            "REMOTE_ADDR": "10.0.0.3",
        }
    )

    await registry._handle_connect(client)
    assert client.disconnect_called is True

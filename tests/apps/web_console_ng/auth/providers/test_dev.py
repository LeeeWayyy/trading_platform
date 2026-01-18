from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

import apps.web_console_ng.auth.providers.dev as dev_module
from apps.web_console_ng import config


def _set_dev_config(monkeypatch: pytest.MonkeyPatch, auth_type: str = "dev") -> None:
    monkeypatch.setattr(config, "AUTH_TYPE", auth_type)
    monkeypatch.setattr(config, "DEV_ROLE", "admin")
    monkeypatch.setattr(config, "DEV_USER_ID", "dev-user")
    monkeypatch.setattr(config, "DEV_STRATEGIES", ["alpha_baseline"])


@pytest.fixture()
def session_store() -> AsyncMock:
    store = AsyncMock()
    store.create_session.return_value = ("cookie-value", "csrf-token")
    return store


@pytest.mark.asyncio()
async def test_auth_type_not_dev(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_dev_config(monkeypatch, auth_type="basic")

    handler = dev_module.DevAuthHandler()
    result = await handler.authenticate(username="admin", password="changeme")

    assert result.success is False
    assert result.error_message == "Dev auth not enabled"


@pytest.mark.asyncio()
async def test_invalid_credentials(monkeypatch: pytest.MonkeyPatch, session_store: AsyncMock) -> None:
    _set_dev_config(monkeypatch)
    monkeypatch.setenv("WEB_CONSOLE_USER", "expected")
    monkeypatch.setenv("WEB_CONSOLE_PASSWORD", "expected-pass")

    with patch.object(dev_module, "get_session_store", return_value=session_store):
        handler = dev_module.DevAuthHandler()
        result = await handler.authenticate(username="wrong", password="wrong")

    assert result.success is False
    assert result.error_message == "Invalid credentials"
    session_store.create_session.assert_not_called()


@pytest.mark.asyncio()
async def test_success_creates_session(
    monkeypatch: pytest.MonkeyPatch, session_store: AsyncMock
) -> None:
    _set_dev_config(monkeypatch)
    monkeypatch.setenv("WEB_CONSOLE_USER", "admin")
    monkeypatch.setenv("WEB_CONSOLE_PASSWORD", "changeme")

    with patch.object(dev_module, "get_session_store", return_value=session_store):
        handler = dev_module.DevAuthHandler()
        result = await handler.authenticate(
            username="admin",
            password="changeme",
            client_ip="10.0.0.2",
            user_agent="pytest",
        )

    assert result.success is True
    assert result.cookie_value == "cookie-value"
    assert result.csrf_token == "csrf-token"
    assert result.user_data
    assert result.user_data["auth_method"] == "dev"
    assert result.user_data["username"] == "admin"
    assert result.user_data["user_id"] == "dev-user"
    assert result.user_data["role"] == "admin"
    assert result.user_data["strategies"] == ["alpha_baseline"]
    assert result.requires_mfa is False

    session_store.create_session.assert_awaited_once()
    _, kwargs = session_store.create_session.await_args
    assert kwargs.get("client_ip") == "10.0.0.2"
    assert kwargs.get("device_info", {}).get("user_agent") == "pytest"


@pytest.mark.asyncio()
async def test_session_creation_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_dev_config(monkeypatch)
    monkeypatch.setenv("WEB_CONSOLE_USER", "admin")
    monkeypatch.setenv("WEB_CONSOLE_PASSWORD", "changeme")

    failing_store = AsyncMock()
    failing_store.create_session.side_effect = RuntimeError("boom")

    with patch.object(dev_module, "get_session_store", return_value=failing_store):
        handler = dev_module.DevAuthHandler()
        result = await handler.authenticate(username="admin", password="changeme")

    assert result.success is False
    assert result.error_message
    assert "Session creation failed" in result.error_message
    assert "boom" in result.error_message

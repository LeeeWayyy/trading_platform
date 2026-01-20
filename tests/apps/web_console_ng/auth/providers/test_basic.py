from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

import apps.web_console_ng.auth.providers.basic as basic_module
from apps.web_console_ng import config


@pytest.fixture()
def rate_limiter() -> AsyncMock:
    limiter = AsyncMock()
    limiter.check_only.return_value = (False, 0, "allowed")
    limiter.record_failure.return_value = (True, 0, "failure_recorded")
    limiter.clear_on_success.return_value = None
    return limiter


@pytest.fixture()
def session_store() -> AsyncMock:
    store = AsyncMock()
    store.create_session.return_value = ("cookie-value", "csrf-token")
    return store


def _set_basic_config(
    monkeypatch: pytest.MonkeyPatch,
    auth_type: str = "basic",
    debug: bool = True,
    allow_dev_basic_auth: bool = True,
) -> None:
    monkeypatch.setattr(config, "AUTH_TYPE", auth_type)
    monkeypatch.setattr(config, "DEBUG", debug)
    monkeypatch.setattr(config, "ALLOW_DEV_BASIC_AUTH", allow_dev_basic_auth)
    monkeypatch.setattr(config, "DEV_ROLE", "admin")
    monkeypatch.setattr(config, "DEV_USER_ID", "dev-user")
    monkeypatch.setattr(config, "DEV_STRATEGIES", ["alpha_baseline"])


@pytest.mark.asyncio()
async def test_auth_type_not_basic(
    monkeypatch: pytest.MonkeyPatch, rate_limiter: AsyncMock
) -> None:
    _set_basic_config(monkeypatch, auth_type="dev")

    with patch.object(basic_module, "AuthRateLimiter", return_value=rate_limiter):
        handler = basic_module.BasicAuthHandler()
        result = await handler.authenticate(username="admin", password="changeme")

    assert result.success is False
    assert result.error_message == "Basic auth not enabled"


@pytest.mark.asyncio()
async def test_basic_auth_blocked_in_production(
    monkeypatch: pytest.MonkeyPatch, rate_limiter: AsyncMock
) -> None:
    _set_basic_config(monkeypatch, auth_type="basic", debug=False, allow_dev_basic_auth=True)

    with patch.object(basic_module, "AuthRateLimiter", return_value=rate_limiter):
        handler = basic_module.BasicAuthHandler()
        result = await handler.authenticate(username="admin", password="changeme")

    assert result.success is False
    assert "disabled in production" in (result.error_message or "")
    rate_limiter.check_only.assert_not_called()


@pytest.mark.asyncio()
async def test_dev_basic_auth_disabled(
    monkeypatch: pytest.MonkeyPatch, rate_limiter: AsyncMock
) -> None:
    _set_basic_config(monkeypatch, auth_type="basic", debug=True, allow_dev_basic_auth=False)

    with patch.object(basic_module, "AuthRateLimiter", return_value=rate_limiter):
        handler = basic_module.BasicAuthHandler()
        result = await handler.authenticate(username="admin", password="changeme")

    assert result.success is False
    assert result.error_message == "Basic auth dev credentials are disabled"
    rate_limiter.check_only.assert_not_called()


@pytest.mark.asyncio()
async def test_rate_limited_account_locked(
    monkeypatch: pytest.MonkeyPatch, rate_limiter: AsyncMock
) -> None:
    _set_basic_config(monkeypatch)
    rate_limiter.check_only.return_value = (True, 120, "account_locked")

    with patch.object(basic_module, "AuthRateLimiter", return_value=rate_limiter):
        handler = basic_module.BasicAuthHandler()
        result = await handler.authenticate(username="admin", password="changeme")

    assert result.success is False
    assert result.locked_out is True
    assert result.lockout_remaining == 120
    assert result.error_message == "Account temporarily locked"


@pytest.mark.asyncio()
async def test_rate_limited_ip(monkeypatch: pytest.MonkeyPatch, rate_limiter: AsyncMock) -> None:
    _set_basic_config(monkeypatch)
    rate_limiter.check_only.return_value = (True, 45, "ip_rate_limit")

    with patch.object(basic_module, "AuthRateLimiter", return_value=rate_limiter):
        handler = basic_module.BasicAuthHandler()
        result = await handler.authenticate(username="admin", password="changeme")

    assert result.success is False
    assert result.rate_limited is True
    assert result.retry_after == 45
    assert result.error_message == "Too many attempts"


@pytest.mark.asyncio()
async def test_invalid_credentials_records_failure(
    monkeypatch: pytest.MonkeyPatch, rate_limiter: AsyncMock
) -> None:
    _set_basic_config(monkeypatch)
    monkeypatch.setenv("WEB_CONSOLE_USER", "expected")
    monkeypatch.setenv("WEB_CONSOLE_PASSWORD", "expected-pass")

    with patch.object(basic_module, "AuthRateLimiter", return_value=rate_limiter):
        handler = basic_module.BasicAuthHandler()
        result = await handler.authenticate(username="wrong", password="wrong")

    assert result.success is False
    assert result.error_message == "Invalid credentials"
    rate_limiter.record_failure.assert_awaited_once_with("127.0.0.1", "wrong")


@pytest.mark.asyncio()
async def test_invalid_credentials_lockout(
    monkeypatch: pytest.MonkeyPatch, rate_limiter: AsyncMock
) -> None:
    _set_basic_config(monkeypatch)
    monkeypatch.setenv("WEB_CONSOLE_USER", "expected")
    monkeypatch.setenv("WEB_CONSOLE_PASSWORD", "expected-pass")
    rate_limiter.record_failure.return_value = (False, 300, "account_locked_now")

    with patch.object(basic_module, "AuthRateLimiter", return_value=rate_limiter):
        handler = basic_module.BasicAuthHandler()
        result = await handler.authenticate(username="wrong", password="wrong")

    assert result.success is False
    assert result.locked_out is True
    assert result.lockout_remaining == 300
    assert result.error_message == "Account locked due to too many failed attempts"


@pytest.mark.asyncio()
async def test_success_creates_session(
    monkeypatch: pytest.MonkeyPatch,
    rate_limiter: AsyncMock,
    session_store: AsyncMock,
) -> None:
    _set_basic_config(monkeypatch)
    monkeypatch.setenv("WEB_CONSOLE_USER", "admin")
    monkeypatch.setenv("WEB_CONSOLE_PASSWORD", "changeme")

    with (
        patch.object(basic_module, "AuthRateLimiter", return_value=rate_limiter),
        patch.object(basic_module, "get_session_store", return_value=session_store),
    ):
        handler = basic_module.BasicAuthHandler()
        result = await handler.authenticate(
            username="admin",
            password="changeme",
            client_ip="10.0.0.1",
            user_agent="pytest",
        )

    assert result.success is True
    assert result.cookie_value == "cookie-value"
    assert result.csrf_token == "csrf-token"
    assert result.user_data
    assert result.user_data["auth_method"] == "basic"
    assert result.user_data["username"] == "admin"
    assert result.user_data["user_id"] == "dev-user"
    assert result.user_data["role"] == "admin"
    assert result.user_data["strategies"] == ["alpha_baseline"]
    assert result.requires_mfa is False

    rate_limiter.clear_on_success.assert_awaited_once_with("admin")
    session_store.create_session.assert_awaited_once()

    args, kwargs = session_store.create_session.await_args
    user_data = kwargs.get("user_data", {})
    device_info = kwargs.get("device_info", {})
    assert user_data["auth_method"] == "basic"
    assert device_info.get("user_agent") == "pytest"
    assert kwargs.get("client_ip") == "10.0.0.1"

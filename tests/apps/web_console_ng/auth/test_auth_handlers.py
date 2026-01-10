from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock
from urllib.parse import parse_qs, urlparse

import pytest

from apps.web_console_ng import config
from apps.web_console_ng.auth.auth_router import get_auth_handler
from apps.web_console_ng.auth.providers.basic import BasicAuthHandler
from apps.web_console_ng.auth.providers.dev import DevAuthHandler
from apps.web_console_ng.auth.providers.mtls import MTLSAuthHandler
from apps.web_console_ng.auth.providers.oauth2 import OAuth2AuthHandler


@pytest.mark.asyncio()
async def test_dev_auth_valid_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "AUTH_TYPE", "dev")
    monkeypatch.setenv("WEB_CONSOLE_USER", "admin")
    monkeypatch.setenv("WEB_CONSOLE_PASSWORD", "admin123")
    handler = DevAuthHandler()

    mock_store = AsyncMock()
    mock_store.create_session.return_value = ("cookie_dev", "csrf_dev")
    monkeypatch.setattr(
        "apps.web_console_ng.auth.providers.dev.get_session_store",
        lambda: mock_store,
    )

    result = await handler.authenticate(
        username="admin",
        password="admin123",
        client_ip="10.0.0.1",
        user_agent="test-agent",
    )

    assert result.success
    assert result.cookie_value == "cookie_dev"
    assert result.csrf_token == "csrf_dev"
    assert result.user_data is not None
    assert result.user_data["role"] == "admin"


@pytest.mark.asyncio()
async def test_dev_auth_invalid_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "AUTH_TYPE", "dev")
    handler = DevAuthHandler()

    result = await handler.authenticate(username="admin", password="wrong")

    assert not result.success
    assert result.error_message == "Invalid credentials"


@pytest.mark.asyncio()
async def test_basic_auth_rate_limit_blocks(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "AUTH_TYPE", "basic")
    monkeypatch.setattr(config, "DEBUG", True)  # Required for basic auth
    monkeypatch.setattr(config, "ALLOW_DEV_BASIC_AUTH", True)  # Required for basic auth
    handler = BasicAuthHandler()

    rate_limiter = SimpleNamespace(
        check_only=AsyncMock(return_value=(True, 120, "account_locked")),
        record_failure=AsyncMock(),
        clear_on_success=AsyncMock(),
    )
    handler._rate_limiter = rate_limiter  # type: ignore[assignment]

    result = await handler.authenticate(
        username="admin",
        password="admin123",
        client_ip="10.0.0.5",
    )

    rate_limiter.check_only.assert_awaited_once_with("10.0.0.5", "admin")
    rate_limiter.record_failure.assert_not_awaited()

    assert not result.success
    assert result.locked_out
    assert result.lockout_remaining == 120


@pytest.mark.asyncio()
async def test_mtls_auth_uses_client_dn(monkeypatch: pytest.MonkeyPatch) -> None:
    import ipaddress

    monkeypatch.setattr(config, "AUTH_TYPE", "mtls")
    # Add test IP to trusted proxies
    monkeypatch.setattr(config, "TRUSTED_PROXY_IPS", [ipaddress.ip_address("10.0.0.1")])
    handler = MTLSAuthHandler()

    request = SimpleNamespace(
        headers={"X-SSL-Client-Verify": "SUCCESS"},
        client=SimpleNamespace(host="10.0.0.1"),
    )

    mock_store = AsyncMock()
    mock_store.create_session.return_value = ("cookie_mtls", "csrf_mtls")
    monkeypatch.setattr(
        "apps.web_console_ng.auth.providers.mtls.get_session_store",
        lambda: mock_store,
    )

    result = await handler.authenticate(
        request=request,
        client_dn="/CN=trader-user/OU=trader/O=TradingPlatform",
    )

    assert result.success
    assert result.cookie_value == "cookie_mtls"
    assert result.user_data is not None
    assert result.user_data["username"] == "trader-user"
    assert result.user_data["role"] == "trader"


@pytest.mark.asyncio()
async def test_oauth2_get_authorization_url(monkeypatch: pytest.MonkeyPatch) -> None:
    handler = OAuth2AuthHandler()

    mock_redis = AsyncMock()
    handler._redis = mock_redis  # Use private attribute, not property

    url = await handler.get_authorization_url()

    parsed = urlparse(url)
    params = parse_qs(parsed.query)

    assert params["response_type"] == ["code"]
    assert "code_challenge" in params
    assert params["code_challenge_method"] == ["S256"]
    assert "state" in params

    state = params["state"][0]
    mock_redis.setex.assert_called_once()
    assert mock_redis.setex.call_args.args[0] == f"{handler.OAUTH2_STATE_PREFIX}{state}"


def test_auth_router() -> None:
    assert isinstance(get_auth_handler("dev"), DevAuthHandler)
    assert isinstance(get_auth_handler("basic"), BasicAuthHandler)
    assert isinstance(get_auth_handler("mtls"), MTLSAuthHandler)
    assert isinstance(get_auth_handler("oauth2"), OAuth2AuthHandler)

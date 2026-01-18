from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

import apps.web_console_ng.auth.providers.mtls as mtls_module
from apps.web_console_ng import config


class _DummyRequest:
    def __init__(self, headers: dict[str, str] | None = None, client_host: str = "127.0.0.1") -> None:
        self.headers = headers or {}
        self.client = SimpleNamespace(host=client_host)


@pytest.fixture()
def session_store() -> AsyncMock:
    store = AsyncMock()
    store.create_session.return_value = ("cookie-value", "csrf-token")
    return store


def _set_mtls_config(monkeypatch: pytest.MonkeyPatch, auth_type: str = "mtls") -> None:
    monkeypatch.setattr(config, "AUTH_TYPE", auth_type)


@pytest.mark.asyncio()
async def test_try_auto_login_not_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_mtls_config(monkeypatch, auth_type="dev")

    handler = mtls_module.MTLSAuthHandler()
    result = await handler.try_auto_login(_DummyRequest())

    assert result.success is False
    assert result.error_message == "mTLS auth not enabled"


@pytest.mark.asyncio()
async def test_try_auto_login_untrusted_proxy(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_mtls_config(monkeypatch)

    handler = mtls_module.MTLSAuthHandler()
    request = _DummyRequest(headers={"X-SSL-Client-Verify": "SUCCESS", "X-SSL-Client-DN": "/CN=alice"})

    with patch.object(mtls_module, "is_trusted_ip", return_value=False):
        result = await handler.try_auto_login(request)

    assert result.success is False
    assert result.error_message == "Client certificate required for mTLS authentication."


@pytest.mark.asyncio()
async def test_try_auto_login_verification_failed(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_mtls_config(monkeypatch)

    handler = mtls_module.MTLSAuthHandler()
    request = _DummyRequest(headers={"X-SSL-Client-Verify": "FAIL", "X-SSL-Client-DN": "/CN=alice"})

    with patch.object(mtls_module, "is_trusted_ip", return_value=True):
        result = await handler.try_auto_login(request)

    assert result.success is False
    assert result.error_message == "Certificate verification failed: FAIL"


@pytest.mark.asyncio()
async def test_try_auto_login_missing_dn(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_mtls_config(monkeypatch)

    handler = mtls_module.MTLSAuthHandler()
    request = _DummyRequest(headers={"X-SSL-Client-Verify": "SUCCESS"})

    with patch.object(mtls_module, "is_trusted_ip", return_value=True):
        result = await handler.try_auto_login(request)

    assert result.success is False
    assert result.error_message == "Client certificate required for mTLS authentication."


@pytest.mark.asyncio()
async def test_try_auto_login_success_with_expiry_warning(
    monkeypatch: pytest.MonkeyPatch, session_store: AsyncMock
) -> None:
    _set_mtls_config(monkeypatch)

    handler = mtls_module.MTLSAuthHandler()
    expires = (datetime.now(UTC) + timedelta(days=10)).strftime("%b %d %H:%M:%S %Y GMT")
    request = _DummyRequest(
        headers={
            "X-SSL-Client-Verify": "SUCCESS",
            "X-SSL-Client-DN": "/CN=alice/OU=admin",
            "X-SSL-Client-Not-After": expires,
            "user-agent": "pytest",
        },
        client_host="10.0.0.10",
    )

    with (
        patch.object(mtls_module, "is_trusted_ip", return_value=True),
        patch.object(mtls_module, "extract_trusted_client_ip", return_value="203.0.113.5"),
        patch.object(mtls_module, "get_session_store", return_value=session_store),
    ):
        result = await handler.try_auto_login(request)

    assert result.success is True
    assert result.warning_message
    assert "expires in" in result.warning_message
    assert result.user_data
    assert result.user_data["auth_method"] == "mtls"


def test_check_certificate_expiry_expired() -> None:
    handler = mtls_module.MTLSAuthHandler()
    expires = (datetime.now(UTC) - timedelta(days=1)).strftime("%b %d %H:%M:%S %Y GMT")
    request = _DummyRequest(headers={"X-SSL-Client-Not-After": expires})

    warning = handler._check_certificate_expiry(request)

    assert warning == "Your certificate has expired. Please renew it immediately."


def test_check_certificate_expiry_invalid_date() -> None:
    handler = mtls_module.MTLSAuthHandler()
    request = _DummyRequest(headers={"X-SSL-Client-Not-After": "not-a-date"})

    warning = handler._check_certificate_expiry(request)

    assert warning is None


@pytest.mark.asyncio()
async def test_authenticate_not_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_mtls_config(monkeypatch, auth_type="dev")

    handler = mtls_module.MTLSAuthHandler()
    result = await handler.authenticate()

    assert result.success is False
    assert result.error_message == "mTLS auth not enabled"


@pytest.mark.asyncio()
async def test_authenticate_missing_request_context(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_mtls_config(monkeypatch)

    handler = mtls_module.MTLSAuthHandler()
    monkeypatch.setattr(mtls_module.app, "storage", SimpleNamespace(), raising=False)

    result = await handler.authenticate()

    assert result.success is False
    assert result.error_message == "No request context"


@pytest.mark.asyncio()
async def test_authenticate_untrusted_proxy(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_mtls_config(monkeypatch)

    handler = mtls_module.MTLSAuthHandler()
    request = _DummyRequest(headers={"X-SSL-Client-Verify": "SUCCESS"})

    with patch.object(mtls_module, "is_trusted_ip", return_value=False):
        result = await handler.authenticate(request=request)

    assert result.success is False
    assert result.error_message == "Untrusted source"


@pytest.mark.asyncio()
async def test_authenticate_missing_or_invalid_headers(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_mtls_config(monkeypatch)

    handler = mtls_module.MTLSAuthHandler()
    request = _DummyRequest(headers={"X-SSL-Client-Verify": "FAIL"})

    with patch.object(mtls_module, "is_trusted_ip", return_value=True):
        result = await handler.authenticate(request=request)

    assert result.success is False
    assert result.error_message == "Client certificate not verified"


@pytest.mark.asyncio()
async def test_authenticate_invalid_dn(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_mtls_config(monkeypatch)

    handler = mtls_module.MTLSAuthHandler()
    request = _DummyRequest(headers={"X-SSL-Client-Verify": "SUCCESS", "X-SSL-Client-DN": "/OU=admin"})

    with patch.object(mtls_module, "is_trusted_ip", return_value=True):
        result = await handler.authenticate(request=request)

    assert result.success is False
    assert result.error_message == "Invalid certificate DN format"


@pytest.mark.asyncio()
async def test_authenticate_success(monkeypatch: pytest.MonkeyPatch, session_store: AsyncMock) -> None:
    _set_mtls_config(monkeypatch)

    handler = mtls_module.MTLSAuthHandler()
    request = _DummyRequest(
        headers={
            "X-SSL-Client-Verify": "SUCCESS",
            "X-SSL-Client-DN": "/CN=alice/OU=trader/O=TradingPlatform",
            "X-SSL-Client-Serial": "abc123",
        },
        client_host="10.0.0.9",
    )

    with (
        patch.object(mtls_module, "is_trusted_ip", return_value=True),
        patch.object(mtls_module, "get_session_store", return_value=session_store),
    ):
        result = await handler.authenticate(
            request=request,
            client_ip="198.51.100.9",
            user_agent="pytest",
        )

    assert result.success is True
    assert result.cookie_value == "cookie-value"
    assert result.csrf_token == "csrf-token"
    assert result.user_data
    assert result.user_data["username"] == "alice"
    assert result.user_data["role"] == "trader"
    assert result.user_data["client_dn"] == "/CN=alice/OU=trader/O=TradingPlatform"
    assert result.user_data["client_serial"] == "abc123"

    session_store.create_session.assert_awaited_once()
    _, kwargs = session_store.create_session.await_args
    assert kwargs.get("client_ip") == "198.51.100.9"
    assert kwargs.get("device_info", {}).get("user_agent") == "pytest"

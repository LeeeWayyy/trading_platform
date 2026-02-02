"""Unit tests for auth middleware helpers."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from starlette.requests import Request
from starlette.responses import Response

from apps.web_console_ng.auth import middleware as middleware_module
from apps.web_console_ng.auth.session_store import SessionValidationError


@pytest.fixture()
def make_request() -> callable:
    def _make_request(
        *,
        path: str = "/",
        headers: list[tuple[bytes, bytes]] | None = None,
        client: tuple[str, int] = ("203.0.113.10", 1234),
        cookies: dict[str, str] | None = None,
    ) -> Request:
        scope = {
            "type": "http",
            "headers": headers or [],
            "client": client,
            "path": path,
            "scheme": "http",
            "query_string": b"",
        }
        req = Request(scope)
        # Inject cookies if provided
        if cookies:
            req._cookies = cookies
        return req

    return _make_request


@pytest.fixture()
def mock_storage_user() -> dict[str, Any]:
    """Fixture providing a mutable dict to simulate app.storage.user."""
    return {}


@pytest.fixture()
def mock_app(mock_storage_user: dict[str, Any]) -> SimpleNamespace:
    """Fixture providing a mock app with storage."""
    storage_user_obj = MagicMock()
    storage_user_obj.__getitem__ = lambda self, key: mock_storage_user[key]
    storage_user_obj.__setitem__ = lambda self, key, value: mock_storage_user.__setitem__(
        key, value
    )
    storage_user_obj.get = lambda key, default=None: mock_storage_user.get(key, default)
    storage_user_obj.clear = lambda: mock_storage_user.clear()
    return SimpleNamespace(storage=SimpleNamespace(user=storage_user_obj))


def test_get_request_from_storage_prefers_contextvar(
    make_request: callable, monkeypatch: pytest.MonkeyPatch
) -> None:
    request = make_request(path="/health")

    def _return_request() -> Request:
        return request

    dummy_storage = SimpleNamespace(request_contextvar=SimpleNamespace(get=_return_request))
    dummy_ui = SimpleNamespace(context=SimpleNamespace(client=SimpleNamespace(request=None)))
    import nicegui

    monkeypatch.setattr(nicegui, "storage", dummy_storage, raising=False)
    monkeypatch.setattr(nicegui, "ui", dummy_ui, raising=False)

    assert middleware_module._get_request_from_storage() is request


def test_get_request_from_storage_falls_back_in_debug(
    make_request: callable, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _raise_lookup() -> Request:
        raise LookupError

    dummy_storage = SimpleNamespace(request_contextvar=SimpleNamespace(get=_raise_lookup))
    dummy_ui = SimpleNamespace(context=SimpleNamespace(client=SimpleNamespace()))
    import nicegui

    monkeypatch.setattr(nicegui, "storage", dummy_storage, raising=False)
    monkeypatch.setattr(nicegui, "ui", dummy_ui, raising=False)
    monkeypatch.setattr(middleware_module.config, "DEBUG", True)

    request = middleware_module._get_request_from_storage()

    assert request.client is not None
    assert request.client.host == "192.0.2.1"
    assert request.url.path == "/"


def test_validate_mtls_request_rejects_untrusted_ip(
    make_request: callable, monkeypatch: pytest.MonkeyPatch
) -> None:
    request = make_request(headers=[(b"x-ssl-client-verify", b"SUCCESS")])
    monkeypatch.setattr(middleware_module, "is_trusted_ip", lambda _ip: False)

    assert middleware_module._validate_mtls_request(request, {"client_dn": "CN=user"}) is False


def test_validate_mtls_request_accepts_matching_dn(
    make_request: callable, monkeypatch: pytest.MonkeyPatch
) -> None:
    headers = [
        (b"x-ssl-client-verify", b"SUCCESS"),
        (b"x-ssl-client-dn", b"CN=user"),
    ]
    request = make_request(headers=headers)
    monkeypatch.setattr(middleware_module, "is_trusted_ip", lambda _ip: True)

    assert middleware_module._validate_mtls_request(request, {"client_dn": "CN=user"}) is True


def test_validate_mtls_request_rejects_mismatched_dn(
    make_request: callable, monkeypatch: pytest.MonkeyPatch
) -> None:
    headers = [
        (b"x-ssl-client-verify", b"SUCCESS"),
        (b"x-ssl-client-dn", b"CN=other"),
    ]
    request = make_request(headers=headers)
    monkeypatch.setattr(middleware_module, "is_trusted_ip", lambda _ip: True)

    assert middleware_module._validate_mtls_request(request, {"client_dn": "CN=user"}) is False


def test_redirect_to_login_sets_storage_and_navigates(
    make_request: callable, monkeypatch: pytest.MonkeyPatch
) -> None:
    storage_user: dict[str, str] = {}
    dummy_app = SimpleNamespace(storage=SimpleNamespace(user=storage_user))
    dummy_ui = SimpleNamespace(navigate=SimpleNamespace(to=MagicMock()))
    monkeypatch.setattr(middleware_module, "app", dummy_app)
    monkeypatch.setattr(middleware_module, "ui", dummy_ui)

    request = make_request(path="/risk")

    middleware_module._redirect_to_login(request)

    assert storage_user["redirect_after_login"] == "/risk"
    assert storage_user["login_reason"] == "session_expired"
    dummy_ui.navigate.to.assert_called_once_with("/login")


def test_get_request_from_storage_uses_ui_context_fallback(
    make_request: callable, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test that _get_request_from_storage falls back to ui.context.client.request."""
    request = make_request(path="/dashboard")

    def _raise_lookup() -> Request:
        raise LookupError

    # contextvar raises, but ui.context.client.request is available
    dummy_storage = SimpleNamespace(request_contextvar=SimpleNamespace(get=_raise_lookup))
    dummy_ui = SimpleNamespace(context=SimpleNamespace(client=SimpleNamespace(request=request)))
    import nicegui

    monkeypatch.setattr(nicegui, "storage", dummy_storage, raising=False)
    monkeypatch.setattr(nicegui, "ui", dummy_ui, raising=False)

    result = middleware_module._get_request_from_storage()
    assert result is request


def test_get_request_from_storage_raises_in_production(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test that _get_request_from_storage raises RuntimeError in production without context."""

    def _raise_lookup() -> Request:
        raise LookupError

    # Both context sources fail
    dummy_storage = SimpleNamespace(request_contextvar=SimpleNamespace(get=_raise_lookup))
    # ui.context.client has no request attribute
    dummy_ui = SimpleNamespace(context=SimpleNamespace(client=SimpleNamespace()))
    import nicegui

    monkeypatch.setattr(nicegui, "storage", dummy_storage, raising=False)
    monkeypatch.setattr(nicegui, "ui", dummy_ui, raising=False)
    monkeypatch.setattr(middleware_module.config, "DEBUG", False)

    with pytest.raises(RuntimeError, match="No request context available"):
        middleware_module._get_request_from_storage()


def test_get_current_user_returns_user_from_storage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test get_current_user returns stored user data."""
    user_data = {"role": "admin", "username": "test_admin"}
    storage_user = {"user": user_data}
    dummy_app = SimpleNamespace(storage=SimpleNamespace(user=storage_user))
    monkeypatch.setattr(middleware_module, "app", dummy_app)

    result = middleware_module.get_current_user()
    assert result == user_data


def test_get_current_user_returns_default_guest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test get_current_user returns Guest when no user in storage."""
    storage_user: dict[str, Any] = {}
    dummy_app = SimpleNamespace(storage=SimpleNamespace(user=storage_user))
    monkeypatch.setattr(middleware_module, "app", dummy_app)

    result = middleware_module.get_current_user()
    assert result == {"role": "viewer", "username": "Guest"}


def test_validate_mtls_request_rejects_missing_verification(
    make_request: callable, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test mTLS validation rejects requests with missing or failed verify header."""
    # No X-SSL-Client-Verify header
    request = make_request()
    monkeypatch.setattr(middleware_module, "is_trusted_ip", lambda _ip: True)

    assert middleware_module._validate_mtls_request(request, {"client_dn": "CN=user"}) is False


def test_validate_mtls_request_rejects_failed_verification(
    make_request: callable, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test mTLS validation rejects requests with FAILED verification."""
    headers = [
        (b"x-ssl-client-verify", b"FAILED"),
        (b"x-ssl-client-dn", b"CN=user"),
    ]
    request = make_request(headers=headers)
    monkeypatch.setattr(middleware_module, "is_trusted_ip", lambda _ip: True)

    assert middleware_module._validate_mtls_request(request, {"client_dn": "CN=user"}) is False


def test_validate_mtls_request_rejects_no_session_dn(
    make_request: callable, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test mTLS validation rejects when user_data has no client_dn."""
    headers = [
        (b"x-ssl-client-verify", b"SUCCESS"),
        (b"x-ssl-client-dn", b"CN=user"),
    ]
    request = make_request(headers=headers)
    monkeypatch.setattr(middleware_module, "is_trusted_ip", lambda _ip: True)

    # User data without client_dn
    assert middleware_module._validate_mtls_request(request, {}) is False


@pytest.mark.asyncio()
async def test_validate_session_and_get_user_no_cookie(
    make_request: callable, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test _validate_session_and_get_user returns None when no cookie present."""
    request = make_request()

    # Mock CookieConfig - patch at the actual import location
    mock_cookie_cfg = MagicMock()
    mock_cookie_cfg.get_cookie_name.return_value = "session_cookie"
    mock_cookie_config_cls = MagicMock(from_env=MagicMock(return_value=mock_cookie_cfg))

    with patch("apps.web_console_ng.auth.cookie_config.CookieConfig", mock_cookie_config_cls):
        user_data, cookie_value = await middleware_module._validate_session_and_get_user(request)

    assert user_data is None
    assert cookie_value is None


@pytest.mark.asyncio()
async def test_validate_session_and_get_user_valid_session(
    make_request: callable, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test _validate_session_and_get_user returns user data for valid session."""
    request = make_request(cookies={"trading_session": "valid_cookie_value"})

    # Mock CookieConfig
    mock_cookie_cfg = MagicMock()
    mock_cookie_cfg.get_cookie_name.return_value = "trading_session"

    # Mock session store
    mock_session_store = AsyncMock()
    mock_session_store.validate_session.return_value = {
        "user": {"username": "test_user", "role": "admin"}
    }

    monkeypatch.setattr(middleware_module.config, "AUTH_TYPE", "cookie")
    monkeypatch.setattr(middleware_module.config, "TRUSTED_PROXY_IPS", [])

    with patch("apps.web_console_ng.auth.cookie_config.CookieConfig") as mock_cc:
        mock_cc.from_env.return_value = mock_cookie_cfg
        with patch(
            "apps.web_console_ng.auth.middleware.get_session_store",
            return_value=mock_session_store,
        ):
            user_data, cookie_value = await middleware_module._validate_session_and_get_user(
                request
            )

    assert user_data == {"username": "test_user", "role": "admin"}
    assert cookie_value == "valid_cookie_value"


@pytest.mark.asyncio()
async def test_validate_session_and_get_user_invalid_session(
    make_request: callable, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test _validate_session_and_get_user returns None for invalid session."""
    request = make_request(cookies={"trading_session": "invalid_cookie_value"})

    mock_cookie_cfg = MagicMock()
    mock_cookie_cfg.get_cookie_name.return_value = "trading_session"

    mock_session_store = AsyncMock()
    mock_session_store.validate_session.return_value = None

    monkeypatch.setattr(middleware_module.config, "AUTH_TYPE", "cookie")
    monkeypatch.setattr(middleware_module.config, "TRUSTED_PROXY_IPS", [])

    with patch("apps.web_console_ng.auth.cookie_config.CookieConfig") as mock_cc:
        mock_cc.from_env.return_value = mock_cookie_cfg
        with patch(
            "apps.web_console_ng.auth.middleware.get_session_store",
            return_value=mock_session_store,
        ):
            user_data, cookie_value = await middleware_module._validate_session_and_get_user(
                request
            )

    assert user_data is None
    assert cookie_value == "invalid_cookie_value"


@pytest.mark.asyncio()
async def test_validate_session_and_get_user_mtls_validation_failure(
    make_request: callable, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Test _validate_session_and_get_user returns None when mTLS validation fails."""
    headers = [(b"x-ssl-client-verify", b"SUCCESS"), (b"x-ssl-client-dn", b"CN=other")]
    request = make_request(cookies={"trading_session": "valid_cookie_value"}, headers=headers)

    mock_cookie_cfg = MagicMock()
    mock_cookie_cfg.get_cookie_name.return_value = "trading_session"

    mock_session_store = AsyncMock()
    mock_session_store.validate_session.return_value = {
        "user": {"username": "test_user", "role": "admin", "client_dn": "CN=user"}
    }

    monkeypatch.setattr(middleware_module.config, "AUTH_TYPE", "mtls")
    monkeypatch.setattr(middleware_module.config, "TRUSTED_PROXY_IPS", [])
    monkeypatch.setattr(middleware_module, "is_trusted_ip", lambda _ip: True)

    with patch("apps.web_console_ng.auth.cookie_config.CookieConfig") as mock_cc:
        mock_cc.from_env.return_value = mock_cookie_cfg
        with patch(
            "apps.web_console_ng.auth.middleware.get_session_store",
            return_value=mock_session_store,
        ):
            user_data, cookie_value = await middleware_module._validate_session_and_get_user(
                request
            )

    # mTLS validation fails due to DN mismatch
    assert user_data is None
    assert cookie_value == "valid_cookie_value"


class TestAuthMiddleware:
    """Tests for AuthMiddleware."""

    @pytest.mark.asyncio()
    async def test_dispatch_exempt_paths(
        self, make_request: callable, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Test AuthMiddleware skips exempt paths."""
        middleware = middleware_module.AuthMiddleware(app=MagicMock())

        for path in [
            "/_nicegui/test",
            "/socket.io/test",
            "/health",
            "/healthz",
            "/readyz",
            "/login",
            "/mfa-verify",
            "/auth/callback",
            "/auth/login",
            "/forgot-password",
            "/dev/login",
        ]:
            request = make_request(path=path)
            call_next = AsyncMock(return_value=Response(status_code=200))

            response = await middleware.dispatch(request, call_next)

            assert response.status_code == 200
            call_next.assert_called_once_with(request)
            call_next.reset_mock()

    @pytest.mark.asyncio()
    async def test_dispatch_mtls_passthrough(
        self, make_request: callable, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Test AuthMiddleware with mTLS SUCCESS header passes through."""
        monkeypatch.setattr(middleware_module.config, "AUTH_TYPE", "mtls")

        headers = [(b"x-ssl-client-verify", b"SUCCESS")]
        request = make_request(path="/dashboard", headers=headers)
        request.state.user = {"username": "mtls_user", "role": "admin"}

        middleware = middleware_module.AuthMiddleware(app=MagicMock())
        call_next = AsyncMock(return_value=Response(status_code=200))

        response = await middleware.dispatch(request, call_next)

        assert response.status_code == 200

    @pytest.mark.asyncio()
    async def test_dispatch_uses_request_state_user(
        self, make_request: callable, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Test AuthMiddleware uses user from request.state if already set."""
        monkeypatch.setattr(middleware_module.config, "AUTH_TYPE", "cookie")

        request = make_request(path="/dashboard")
        request.state.user = {"username": "existing_user", "role": "admin"}

        middleware = middleware_module.AuthMiddleware(app=MagicMock())
        call_next = AsyncMock(return_value=Response(status_code=200))

        response = await middleware.dispatch(request, call_next)

        assert response.status_code == 200
        call_next.assert_called_once_with(request)

    @pytest.mark.asyncio()
    async def test_dispatch_fallback_validates_session(
        self,
        make_request: callable,
        monkeypatch: pytest.MonkeyPatch,
        mock_storage_user: dict[str, Any],
    ) -> None:
        """Test AuthMiddleware validates session when request.state.user not set."""
        monkeypatch.setattr(middleware_module.config, "AUTH_TYPE", "cookie")
        monkeypatch.setattr(middleware_module.config, "TRUSTED_PROXY_IPS", [])

        request = make_request(path="/dashboard", cookies={"trading_session": "valid_cookie"})

        mock_cookie_cfg = MagicMock()
        mock_cookie_cfg.get_cookie_name.return_value = "trading_session"

        mock_session_store = AsyncMock()
        mock_session_store.validate_session.return_value = {
            "user": {"username": "test_user", "role": "admin"}
        }

        # Mock app.storage.user
        storage_obj = MagicMock()
        storage_obj.__setitem__ = lambda self, k, v: mock_storage_user.__setitem__(k, v)
        dummy_app = SimpleNamespace(storage=SimpleNamespace(user=storage_obj))
        monkeypatch.setattr(middleware_module, "app", dummy_app)

        middleware = middleware_module.AuthMiddleware(app=MagicMock())
        call_next = AsyncMock(return_value=Response(status_code=200))

        with patch("apps.web_console_ng.auth.cookie_config.CookieConfig") as mock_cc:
            mock_cc.from_env.return_value = mock_cookie_cfg
            with patch(
                "apps.web_console_ng.auth.middleware.get_session_store",
                return_value=mock_session_store,
            ):
                response = await middleware.dispatch(request, call_next)

        assert response.status_code == 200
        assert request.state.user == {"username": "test_user", "role": "admin"}

    @pytest.mark.asyncio()
    async def test_dispatch_storage_error_handled(
        self,
        make_request: callable,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Test AuthMiddleware handles storage errors gracefully."""
        monkeypatch.setattr(middleware_module.config, "AUTH_TYPE", "cookie")
        monkeypatch.setattr(middleware_module.config, "TRUSTED_PROXY_IPS", [])

        request = make_request(path="/dashboard", cookies={"trading_session": "valid_cookie"})

        mock_cookie_cfg = MagicMock()
        mock_cookie_cfg.get_cookie_name.return_value = "trading_session"

        mock_session_store = AsyncMock()
        mock_session_store.validate_session.return_value = {
            "user": {"username": "test_user", "role": "admin"}
        }

        # Mock app.storage.user that raises RuntimeError
        storage_obj = MagicMock()
        storage_obj.__setitem__ = MagicMock(side_effect=RuntimeError("Storage unavailable"))
        dummy_app = SimpleNamespace(storage=SimpleNamespace(user=storage_obj))
        monkeypatch.setattr(middleware_module, "app", dummy_app)

        middleware = middleware_module.AuthMiddleware(app=MagicMock())
        call_next = AsyncMock(return_value=Response(status_code=200))

        with patch("apps.web_console_ng.auth.cookie_config.CookieConfig") as mock_cc:
            mock_cc.from_env.return_value = mock_cookie_cfg
            with patch(
                "apps.web_console_ng.auth.middleware.get_session_store",
                return_value=mock_session_store,
            ):
                response = await middleware.dispatch(request, call_next)

        # Should still proceed even with storage error
        assert response.status_code == 200

    @pytest.mark.asyncio()
    async def test_dispatch_session_validation_error_returns_503_for_api(
        self,
        make_request: callable,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Test AuthMiddleware returns 503 for API requests on Redis failure."""
        monkeypatch.setattr(middleware_module.config, "AUTH_TYPE", "cookie")
        monkeypatch.setattr(middleware_module.config, "TRUSTED_PROXY_IPS", [])

        headers = [(b"accept", b"application/json")]
        request = make_request(
            path="/dashboard", cookies={"trading_session": "valid_cookie"}, headers=headers
        )

        mock_cookie_cfg = MagicMock()
        mock_cookie_cfg.get_cookie_name.return_value = "trading_session"

        mock_session_store = AsyncMock()
        mock_session_store.validate_session.side_effect = SessionValidationError(
            "Redis unavailable"
        )

        middleware = middleware_module.AuthMiddleware(app=MagicMock())
        call_next = AsyncMock(return_value=Response(status_code=200))

        with patch("apps.web_console_ng.auth.cookie_config.CookieConfig") as mock_cc:
            mock_cc.from_env.return_value = mock_cookie_cfg
            with patch(
                "apps.web_console_ng.auth.middleware.get_session_store",
                return_value=mock_session_store,
            ):
                response = await middleware.dispatch(request, call_next)

        assert response.status_code == 503
        assert response.headers.get("Retry-After") == "5"

    @pytest.mark.asyncio()
    async def test_dispatch_no_user_returns_401_for_api(
        self,
        make_request: callable,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Test AuthMiddleware returns 401 for API requests without valid session."""
        monkeypatch.setattr(middleware_module.config, "AUTH_TYPE", "cookie")
        monkeypatch.setattr(middleware_module.config, "TRUSTED_PROXY_IPS", [])

        headers = [(b"accept", b"application/json")]
        request = make_request(path="/dashboard", headers=headers)

        mock_cookie_cfg = MagicMock()
        mock_cookie_cfg.get_cookie_name.return_value = "trading_session"

        middleware = middleware_module.AuthMiddleware(app=MagicMock())
        call_next = AsyncMock(return_value=Response(status_code=200))

        with patch("apps.web_console_ng.auth.cookie_config.CookieConfig") as mock_cc:
            mock_cc.from_env.return_value = mock_cookie_cfg
            response = await middleware.dispatch(request, call_next)

        assert response.status_code == 401

    @pytest.mark.asyncio()
    async def test_dispatch_no_user_redirects_for_html(
        self,
        make_request: callable,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Test AuthMiddleware redirects to login for HTML requests without session."""
        monkeypatch.setattr(middleware_module.config, "AUTH_TYPE", "cookie")
        monkeypatch.setattr(middleware_module.config, "TRUSTED_PROXY_IPS", [])

        headers = [(b"accept", b"text/html")]
        request = make_request(path="/dashboard", headers=headers)

        mock_cookie_cfg = MagicMock()
        mock_cookie_cfg.get_cookie_name.return_value = "trading_session"
        mock_cookie_cfg.secure = True
        mock_cookie_cfg.path = "/"
        mock_cookie_cfg.domain = None
        mock_cookie_cfg.httponly = True
        mock_cookie_cfg.samesite = "lax"

        middleware = middleware_module.AuthMiddleware(app=MagicMock())
        call_next = AsyncMock(return_value=Response(status_code=200))

        with patch("apps.web_console_ng.auth.cookie_config.CookieConfig") as mock_cc:
            mock_cc.from_env.return_value = mock_cookie_cfg
            response = await middleware.dispatch(request, call_next)

        assert response.status_code == 302
        assert "/login?next=" in response.headers.get("location", "")


class TestSessionMiddleware:
    """Tests for SessionMiddleware."""

    @pytest.mark.asyncio()
    async def test_dispatch_no_cookie(
        self, make_request: callable, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Test SessionMiddleware passes through when no session cookie."""
        mock_cookie_cfg = MagicMock()
        mock_cookie_cfg.get_cookie_name.return_value = "trading_session"

        request = make_request(path="/dashboard")

        middleware = middleware_module.SessionMiddleware(app=MagicMock())
        call_next = AsyncMock(return_value=Response(status_code=200))

        with patch("apps.web_console_ng.auth.cookie_config.CookieConfig") as mock_cc:
            mock_cc.from_env.return_value = mock_cookie_cfg
            response = await middleware.dispatch(request, call_next)

        assert response.status_code == 200

    @pytest.mark.asyncio()
    async def test_dispatch_valid_session(
        self,
        make_request: callable,
        monkeypatch: pytest.MonkeyPatch,
        mock_storage_user: dict[str, Any],
    ) -> None:
        """Test SessionMiddleware sets request.state.user for valid session."""
        monkeypatch.setattr(middleware_module.config, "TRUSTED_PROXY_IPS", [])

        mock_cookie_cfg = MagicMock()
        mock_cookie_cfg.get_cookie_name.return_value = "trading_session"

        mock_session_store = AsyncMock()
        mock_session_store.validate_session.return_value = {
            "user": {"username": "test_user", "role": "admin"}
        }

        # Mock app.storage.user
        storage_obj = MagicMock()
        storage_obj.__setitem__ = lambda self, k, v: mock_storage_user.__setitem__(k, v)
        dummy_app = SimpleNamespace(storage=SimpleNamespace(user=storage_obj))
        monkeypatch.setattr(middleware_module, "app", dummy_app)

        request = make_request(path="/dashboard", cookies={"trading_session": "valid_cookie"})

        middleware = middleware_module.SessionMiddleware(
            app=MagicMock(), session_store=mock_session_store
        )
        call_next = AsyncMock(return_value=Response(status_code=200))

        with patch("apps.web_console_ng.auth.cookie_config.CookieConfig") as mock_cc:
            mock_cc.from_env.return_value = mock_cookie_cfg
            response = await middleware.dispatch(request, call_next)

        assert response.status_code == 200
        assert request.state.user == {"username": "test_user", "role": "admin"}
        assert mock_storage_user.get("logged_in") is True
        assert mock_storage_user.get("session_id") == "valid_cookie"

    @pytest.mark.asyncio()
    async def test_dispatch_invalid_session(
        self, make_request: callable, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Test SessionMiddleware does not set user for invalid session."""
        mock_cookie_cfg = MagicMock()
        mock_cookie_cfg.get_cookie_name.return_value = "trading_session"

        mock_session_store = AsyncMock()
        mock_session_store.validate_session.return_value = None

        request = make_request(path="/dashboard", cookies={"trading_session": "invalid_cookie"})

        middleware = middleware_module.SessionMiddleware(
            app=MagicMock(), session_store=mock_session_store
        )
        call_next = AsyncMock(return_value=Response(status_code=200))

        with patch("apps.web_console_ng.auth.cookie_config.CookieConfig") as mock_cc:
            mock_cc.from_env.return_value = mock_cookie_cfg
            response = await middleware.dispatch(request, call_next)

        assert response.status_code == 200
        assert not hasattr(request.state, "user") or request.state.user is None

    @pytest.mark.asyncio()
    async def test_dispatch_storage_error_handled(
        self, make_request: callable, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Test SessionMiddleware handles storage errors gracefully."""
        mock_cookie_cfg = MagicMock()
        mock_cookie_cfg.get_cookie_name.return_value = "trading_session"

        mock_session_store = AsyncMock()
        mock_session_store.validate_session.return_value = {
            "user": {"username": "test_user", "role": "admin"}
        }

        # Mock app.storage.user that raises RuntimeError
        storage_obj = MagicMock()
        storage_obj.__setitem__ = MagicMock(side_effect=RuntimeError("Storage unavailable"))
        dummy_app = SimpleNamespace(storage=SimpleNamespace(user=storage_obj))
        monkeypatch.setattr(middleware_module, "app", dummy_app)

        request = make_request(path="/dashboard", cookies={"trading_session": "valid_cookie"})

        middleware = middleware_module.SessionMiddleware(
            app=MagicMock(), session_store=mock_session_store
        )
        call_next = AsyncMock(return_value=Response(status_code=200))

        with patch("apps.web_console_ng.auth.cookie_config.CookieConfig") as mock_cc:
            mock_cc.from_env.return_value = mock_cookie_cfg
            response = await middleware.dispatch(request, call_next)

        # Should still proceed even with storage error
        assert response.status_code == 200
        assert request.state.user == {"username": "test_user", "role": "admin"}

    @pytest.mark.asyncio()
    async def test_dispatch_session_validation_error_returns_503_for_api(
        self, make_request: callable, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Test SessionMiddleware returns 503 for API requests on Redis failure."""
        mock_cookie_cfg = MagicMock()
        mock_cookie_cfg.get_cookie_name.return_value = "trading_session"

        mock_session_store = AsyncMock()
        mock_session_store.validate_session.side_effect = SessionValidationError(
            "Redis unavailable"
        )

        headers = [(b"accept", b"application/json")]
        request = make_request(
            path="/dashboard", cookies={"trading_session": "valid_cookie"}, headers=headers
        )

        middleware = middleware_module.SessionMiddleware(
            app=MagicMock(), session_store=mock_session_store
        )
        call_next = AsyncMock(return_value=Response(status_code=200))

        with patch("apps.web_console_ng.auth.cookie_config.CookieConfig") as mock_cc:
            mock_cc.from_env.return_value = mock_cookie_cfg
            response = await middleware.dispatch(request, call_next)

        assert response.status_code == 503
        assert response.headers.get("Retry-After") == "5"

    @pytest.mark.asyncio()
    async def test_dispatch_session_validation_error_passes_through_for_html(
        self, make_request: callable, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Test SessionMiddleware passes through for HTML requests on Redis failure."""
        mock_cookie_cfg = MagicMock()
        mock_cookie_cfg.get_cookie_name.return_value = "trading_session"

        mock_session_store = AsyncMock()
        mock_session_store.validate_session.side_effect = SessionValidationError(
            "Redis unavailable"
        )

        headers = [(b"accept", b"text/html")]
        request = make_request(
            path="/dashboard", cookies={"trading_session": "valid_cookie"}, headers=headers
        )

        middleware = middleware_module.SessionMiddleware(
            app=MagicMock(), session_store=mock_session_store
        )
        call_next = AsyncMock(return_value=Response(status_code=200))

        with patch("apps.web_console_ng.auth.cookie_config.CookieConfig") as mock_cc:
            mock_cc.from_env.return_value = mock_cookie_cfg
            response = await middleware.dispatch(request, call_next)

        # Should proceed to page decorators which will handle error UI
        assert response.status_code == 200

    @pytest.mark.asyncio()
    async def test_dispatch_uses_default_trusted_proxies(
        self, make_request: callable, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Test SessionMiddleware uses config.TRUSTED_PROXY_IPS by default."""
        monkeypatch.setattr(middleware_module.config, "TRUSTED_PROXY_IPS", ["10.0.0.1"])

        middleware = middleware_module.SessionMiddleware(app=MagicMock())

        assert middleware._trusted_proxies == ["10.0.0.1"]


class TestValidateAndGetUserForDecorator:
    """Tests for _validate_and_get_user_for_decorator helper."""

    @pytest.mark.asyncio()
    async def test_uses_cached_user_when_valid(
        self, make_request: callable, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Test decorator validation uses cached user when cache is fresh."""
        now = datetime.now(UTC)
        recent_time = (now - timedelta(seconds=30)).isoformat()

        cached_user = {"username": "cached_user", "role": "admin"}
        storage_data = {
            "user": cached_user,
            "logged_in": True,
            "session_id": "valid_cookie",
            "last_validated_at": recent_time,
        }

        storage_obj = MagicMock()
        storage_obj.get = lambda k, default=None: storage_data.get(k, default)
        storage_obj.clear = lambda: storage_data.clear()
        dummy_app = SimpleNamespace(storage=SimpleNamespace(user=storage_obj))
        monkeypatch.setattr(middleware_module, "app", dummy_app)

        mock_cookie_cfg = MagicMock()
        mock_cookie_cfg.get_cookie_name.return_value = "trading_session"

        request = make_request(path="/dashboard", cookies={"trading_session": "valid_cookie"})

        with patch("apps.web_console_ng.auth.cookie_config.CookieConfig") as mock_cc:
            mock_cc.from_env.return_value = mock_cookie_cfg
            user_data, cookie_value, should_return = (
                await middleware_module._validate_and_get_user_for_decorator(request)
            )

        assert user_data == cached_user
        assert cookie_value == "valid_cookie"
        assert should_return is False

    @pytest.mark.asyncio()
    async def test_clears_cache_when_stale(
        self,
        make_request: callable,
        monkeypatch: pytest.MonkeyPatch,
        mock_storage_user: dict[str, Any],
    ) -> None:
        """Test decorator validation clears cache when stale."""
        # Set up stale cached data (2 minutes old)
        now = datetime.now(UTC)
        old_time = (now - timedelta(seconds=120)).isoformat()

        mock_storage_user.update(
            {
                "user": {"username": "cached_user", "role": "admin"},
                "logged_in": True,
                "session_id": "valid_cookie",
                "last_validated_at": old_time,
            }
        )

        storage_obj = MagicMock()
        storage_obj.get = lambda k, default=None: mock_storage_user.get(k, default)
        storage_obj.clear = lambda: mock_storage_user.clear()
        dummy_app = SimpleNamespace(storage=SimpleNamespace(user=storage_obj))
        monkeypatch.setattr(middleware_module, "app", dummy_app)
        monkeypatch.setattr(middleware_module.config, "AUTH_TYPE", "cookie")
        monkeypatch.setattr(middleware_module.config, "TRUSTED_PROXY_IPS", [])

        mock_cookie_cfg = MagicMock()
        mock_cookie_cfg.get_cookie_name.return_value = "trading_session"

        mock_session_store = AsyncMock()
        mock_session_store.validate_session.return_value = {
            "user": {"username": "revalidated_user", "role": "admin"}
        }

        request = make_request(path="/dashboard", cookies={"trading_session": "valid_cookie"})
        request.state.user = None

        with patch("apps.web_console_ng.auth.cookie_config.CookieConfig") as mock_cc:
            mock_cc.from_env.return_value = mock_cookie_cfg
            with patch(
                "apps.web_console_ng.auth.middleware.get_session_store",
                return_value=mock_session_store,
            ):
                user_data, cookie_value, should_return = (
                    await middleware_module._validate_and_get_user_for_decorator(request)
                )

        # Cache was cleared and session revalidated
        assert (
            len(mock_storage_user) == 0
            or mock_storage_user.get("user") is None
            or user_data != mock_storage_user.get("user")
        )
        assert user_data == {"username": "revalidated_user", "role": "admin"}

    @pytest.mark.asyncio()
    async def test_clears_cache_when_cookie_mismatch(
        self,
        make_request: callable,
        monkeypatch: pytest.MonkeyPatch,
        mock_storage_user: dict[str, Any],
    ) -> None:
        """Test decorator validation clears cache when cookie doesn't match session_id."""
        now = datetime.now(UTC)
        recent_time = (now - timedelta(seconds=30)).isoformat()

        mock_storage_user.update(
            {
                "user": {"username": "cached_user", "role": "admin"},
                "logged_in": True,
                "session_id": "old_cookie",  # Different from request cookie
                "last_validated_at": recent_time,
            }
        )

        storage_obj = MagicMock()
        storage_obj.get = lambda k, default=None: mock_storage_user.get(k, default)
        storage_obj.clear = lambda: mock_storage_user.clear()
        dummy_app = SimpleNamespace(storage=SimpleNamespace(user=storage_obj))
        monkeypatch.setattr(middleware_module, "app", dummy_app)
        monkeypatch.setattr(middleware_module.config, "AUTH_TYPE", "cookie")
        monkeypatch.setattr(middleware_module.config, "TRUSTED_PROXY_IPS", [])

        mock_cookie_cfg = MagicMock()
        mock_cookie_cfg.get_cookie_name.return_value = "trading_session"

        mock_session_store = AsyncMock()
        mock_session_store.validate_session.return_value = {
            "user": {"username": "revalidated_user", "role": "admin"}
        }

        request = make_request(path="/dashboard", cookies={"trading_session": "new_cookie"})
        request.state.user = None

        with patch("apps.web_console_ng.auth.cookie_config.CookieConfig") as mock_cc:
            mock_cc.from_env.return_value = mock_cookie_cfg
            with patch(
                "apps.web_console_ng.auth.middleware.get_session_store",
                return_value=mock_session_store,
            ):
                user_data, cookie_value, should_return = (
                    await middleware_module._validate_and_get_user_for_decorator(request)
                )

        assert user_data == {"username": "revalidated_user", "role": "admin"}

    @pytest.mark.asyncio()
    async def test_redis_error_shows_ui(
        self, make_request: callable, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Test decorator validation shows error UI when Redis unavailable."""
        storage_obj = MagicMock()
        storage_obj.get = lambda k, default=None: default
        dummy_app = SimpleNamespace(storage=SimpleNamespace(user=storage_obj))
        monkeypatch.setattr(middleware_module, "app", dummy_app)
        monkeypatch.setattr(middleware_module.config, "AUTH_TYPE", "cookie")
        monkeypatch.setattr(middleware_module.config, "TRUSTED_PROXY_IPS", [])

        mock_cookie_cfg = MagicMock()
        mock_cookie_cfg.get_cookie_name.return_value = "trading_session"

        mock_session_store = AsyncMock()
        mock_session_store.validate_session.side_effect = SessionValidationError(
            "Redis unavailable"
        )

        request = make_request(path="/dashboard", cookies={"trading_session": "valid_cookie"})

        # Mock ui.card chain
        mock_card = MagicMock()
        mock_card.classes.return_value = mock_card
        mock_card.__enter__ = MagicMock(return_value=mock_card)
        mock_card.__exit__ = MagicMock(return_value=False)
        mock_ui = MagicMock()
        mock_ui.card.return_value = mock_card
        mock_ui.label = MagicMock()
        mock_ui.button = MagicMock()
        monkeypatch.setattr(middleware_module, "ui", mock_ui)

        with patch("apps.web_console_ng.auth.cookie_config.CookieConfig") as mock_cc:
            mock_cc.from_env.return_value = mock_cookie_cfg
            with patch(
                "apps.web_console_ng.auth.middleware.get_session_store",
                return_value=mock_session_store,
            ):
                user_data, cookie_value, should_return = (
                    await middleware_module._validate_and_get_user_for_decorator(request)
                )

        assert user_data is None
        assert cookie_value is None
        assert should_return is True
        mock_ui.card.assert_called_once()

    @pytest.mark.asyncio()
    async def test_no_session_redirects_to_login(
        self, make_request: callable, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Test decorator validation redirects to login when no session."""
        storage_data: dict[str, Any] = {}
        storage_obj = MagicMock()
        storage_obj.get = lambda k, default=None: storage_data.get(k, default)
        storage_obj.__setitem__ = lambda self, k, v: storage_data.__setitem__(k, v)
        dummy_app = SimpleNamespace(storage=SimpleNamespace(user=storage_obj))
        monkeypatch.setattr(middleware_module, "app", dummy_app)
        monkeypatch.setattr(middleware_module.config, "AUTH_TYPE", "cookie")
        monkeypatch.setattr(middleware_module.config, "TRUSTED_PROXY_IPS", [])

        mock_cookie_cfg = MagicMock()
        mock_cookie_cfg.get_cookie_name.return_value = "trading_session"

        mock_ui = MagicMock()
        mock_ui.navigate = MagicMock()
        mock_ui.navigate.to = MagicMock()
        monkeypatch.setattr(middleware_module, "ui", mock_ui)

        # Use a path in ALLOWED_REDIRECT_PATHS to test proper redirect_after_login storage
        request = make_request(path="/risk")

        with patch("apps.web_console_ng.auth.cookie_config.CookieConfig") as mock_cc:
            mock_cc.from_env.return_value = mock_cookie_cfg
            user_data, cookie_value, should_return = (
                await middleware_module._validate_and_get_user_for_decorator(request)
            )

        assert user_data is None
        assert should_return is True
        mock_ui.navigate.to.assert_called_once_with("/login")
        # sanitize_redirect_path returns "/risk" because it's in ALLOWED_REDIRECT_PATHS
        assert storage_data.get("redirect_after_login") == "/risk"

    @pytest.mark.asyncio()
    async def test_expired_session_redirects_with_reason(
        self, make_request: callable, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Test decorator validation redirects with reason for expired session."""
        storage_data: dict[str, Any] = {}
        storage_obj = MagicMock()
        storage_obj.get = lambda k, default=None: storage_data.get(k, default)
        storage_obj.__setitem__ = lambda self, k, v: storage_data.__setitem__(k, v)
        storage_obj.clear = lambda: storage_data.clear()
        dummy_app = SimpleNamespace(storage=SimpleNamespace(user=storage_obj))
        monkeypatch.setattr(middleware_module, "app", dummy_app)
        monkeypatch.setattr(middleware_module.config, "AUTH_TYPE", "cookie")
        monkeypatch.setattr(middleware_module.config, "TRUSTED_PROXY_IPS", [])

        mock_cookie_cfg = MagicMock()
        mock_cookie_cfg.get_cookie_name.return_value = "trading_session"

        mock_session_store = AsyncMock()
        mock_session_store.validate_session.return_value = None

        mock_ui = MagicMock()
        mock_ui.navigate = MagicMock()
        mock_ui.navigate.to = MagicMock()
        monkeypatch.setattr(middleware_module, "ui", mock_ui)

        request = make_request(path="/dashboard", cookies={"trading_session": "expired_cookie"})

        with patch("apps.web_console_ng.auth.cookie_config.CookieConfig") as mock_cc:
            mock_cc.from_env.return_value = mock_cookie_cfg
            with patch(
                "apps.web_console_ng.auth.middleware.get_session_store",
                return_value=mock_session_store,
            ):
                user_data, cookie_value, should_return = (
                    await middleware_module._validate_and_get_user_for_decorator(request)
                )

        assert user_data is None
        assert should_return is True
        mock_ui.navigate.to.assert_called_once_with("/login")
        assert storage_data.get("login_reason") == "session_expired"

    @pytest.mark.asyncio()
    async def test_mfa_pending_redirects_to_mfa_verify(
        self, make_request: callable, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Test decorator validation redirects to MFA verify when MFA pending."""
        storage_data: dict[str, Any] = {}
        storage_obj = MagicMock()
        storage_obj.get = lambda k, default=None: storage_data.get(k, default)
        storage_obj.__setitem__ = lambda self, k, v: storage_data.__setitem__(k, v)
        dummy_app = SimpleNamespace(storage=SimpleNamespace(user=storage_obj))
        monkeypatch.setattr(middleware_module, "app", dummy_app)
        monkeypatch.setattr(middleware_module.config, "AUTH_TYPE", "cookie")
        monkeypatch.setattr(middleware_module.config, "TRUSTED_PROXY_IPS", [])

        mock_cookie_cfg = MagicMock()
        mock_cookie_cfg.get_cookie_name.return_value = "trading_session"

        mock_session_store = AsyncMock()
        mock_session_store.validate_session.return_value = {
            "user": {"username": "test_user", "role": "admin", "mfa_pending": True}
        }

        mock_ui = MagicMock()
        mock_ui.navigate = MagicMock()
        mock_ui.navigate.to = MagicMock()
        monkeypatch.setattr(middleware_module, "ui", mock_ui)

        request = make_request(path="/dashboard", cookies={"trading_session": "valid_cookie"})

        with patch("apps.web_console_ng.auth.cookie_config.CookieConfig") as mock_cc:
            mock_cc.from_env.return_value = mock_cookie_cfg
            with patch(
                "apps.web_console_ng.auth.middleware.get_session_store",
                return_value=mock_session_store,
            ):
                user_data, cookie_value, should_return = (
                    await middleware_module._validate_and_get_user_for_decorator(request)
                )

        assert should_return is True
        mock_ui.navigate.to.assert_called_once_with("/mfa-verify")
        assert storage_data.get("pending_mfa_cookie") == "valid_cookie"

    @pytest.mark.asyncio()
    async def test_mfa_pending_allows_mfa_verify_page(
        self, make_request: callable, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Test decorator validation allows MFA verify page when MFA pending."""
        storage_data: dict[str, Any] = {}
        storage_obj = MagicMock()
        storage_obj.get = lambda k, default=None: storage_data.get(k, default)
        storage_obj.__setitem__ = lambda self, k, v: storage_data.__setitem__(k, v)
        dummy_app = SimpleNamespace(storage=SimpleNamespace(user=storage_obj))
        monkeypatch.setattr(middleware_module, "app", dummy_app)
        monkeypatch.setattr(middleware_module.config, "AUTH_TYPE", "cookie")
        monkeypatch.setattr(middleware_module.config, "TRUSTED_PROXY_IPS", [])

        mock_cookie_cfg = MagicMock()
        mock_cookie_cfg.get_cookie_name.return_value = "trading_session"

        mock_session_store = AsyncMock()
        mock_session_store.validate_session.return_value = {
            "user": {"username": "test_user", "role": "admin", "mfa_pending": True}
        }

        mock_ui = MagicMock()
        monkeypatch.setattr(middleware_module, "ui", mock_ui)

        # User is already on /mfa-verify page
        request = make_request(path="/mfa-verify", cookies={"trading_session": "valid_cookie"})

        with patch("apps.web_console_ng.auth.cookie_config.CookieConfig") as mock_cc:
            mock_cc.from_env.return_value = mock_cookie_cfg
            with patch(
                "apps.web_console_ng.auth.middleware.get_session_store",
                return_value=mock_session_store,
            ):
                user_data, cookie_value, should_return = (
                    await middleware_module._validate_and_get_user_for_decorator(request)
                )

        # Should NOT redirect since already on MFA verify page
        assert user_data == {"username": "test_user", "role": "admin", "mfa_pending": True}
        assert should_return is False

    @pytest.mark.asyncio()
    async def test_uses_request_state_user(
        self, make_request: callable, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Test decorator validation uses user from request.state if set."""
        storage_data: dict[str, Any] = {}
        storage_obj = MagicMock()
        storage_obj.get = lambda k, default=None: storage_data.get(k, default)
        dummy_app = SimpleNamespace(storage=SimpleNamespace(user=storage_obj))
        monkeypatch.setattr(middleware_module, "app", dummy_app)

        mock_cookie_cfg = MagicMock()
        mock_cookie_cfg.get_cookie_name.return_value = "trading_session"

        request = make_request(path="/dashboard", cookies={"trading_session": "valid_cookie"})
        request.state.user = {"username": "state_user", "role": "admin"}

        with patch("apps.web_console_ng.auth.cookie_config.CookieConfig") as mock_cc:
            mock_cc.from_env.return_value = mock_cookie_cfg
            user_data, cookie_value, should_return = (
                await middleware_module._validate_and_get_user_for_decorator(request)
            )

        assert user_data == {"username": "state_user", "role": "admin"}
        assert cookie_value == "valid_cookie"
        assert should_return is False

    @pytest.mark.asyncio()
    async def test_handles_invalid_last_validated_at(
        self, make_request: callable, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Test decorator validation handles invalid last_validated_at timestamp."""
        storage_data = {
            "user": {"username": "cached_user", "role": "admin"},
            "logged_in": True,
            "session_id": "valid_cookie",
            "last_validated_at": "invalid_timestamp",  # Invalid ISO format
        }

        storage_obj = MagicMock()
        storage_obj.get = lambda k, default=None: storage_data.get(k, default)
        storage_obj.clear = lambda: storage_data.clear()
        dummy_app = SimpleNamespace(storage=SimpleNamespace(user=storage_obj))
        monkeypatch.setattr(middleware_module, "app", dummy_app)
        monkeypatch.setattr(middleware_module.config, "AUTH_TYPE", "cookie")
        monkeypatch.setattr(middleware_module.config, "TRUSTED_PROXY_IPS", [])

        mock_cookie_cfg = MagicMock()
        mock_cookie_cfg.get_cookie_name.return_value = "trading_session"

        mock_session_store = AsyncMock()
        mock_session_store.validate_session.return_value = {
            "user": {"username": "revalidated_user", "role": "admin"}
        }

        request = make_request(path="/dashboard", cookies={"trading_session": "valid_cookie"})
        request.state.user = None

        with patch("apps.web_console_ng.auth.cookie_config.CookieConfig") as mock_cc:
            mock_cc.from_env.return_value = mock_cookie_cfg
            with patch(
                "apps.web_console_ng.auth.middleware.get_session_store",
                return_value=mock_session_store,
            ):
                user_data, cookie_value, should_return = (
                    await middleware_module._validate_and_get_user_for_decorator(request)
                )

        # Cache should be treated as stale and revalidated
        assert user_data == {"username": "revalidated_user", "role": "admin"}


class TestRequiresAuth:
    """Tests for @requires_auth decorator."""

    @pytest.mark.asyncio()
    async def test_requires_auth_allows_valid_user(
        self, make_request: callable, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Test @requires_auth allows function execution for valid user."""
        storage_data: dict[str, Any] = {}
        storage_obj = MagicMock()
        storage_obj.get = lambda k, default=None: storage_data.get(k, default)
        storage_obj.__setitem__ = lambda self, k, v: storage_data.__setitem__(k, v)
        dummy_app = SimpleNamespace(storage=SimpleNamespace(user=storage_obj))
        monkeypatch.setattr(middleware_module, "app", dummy_app)
        monkeypatch.setattr(middleware_module.config, "AUTH_TYPE", "cookie")
        monkeypatch.setattr(middleware_module.config, "TRUSTED_PROXY_IPS", [])

        mock_cookie_cfg = MagicMock()
        mock_cookie_cfg.get_cookie_name.return_value = "trading_session"

        mock_session_store = AsyncMock()
        mock_session_store.validate_session.return_value = {
            "user": {"username": "test_user", "role": "admin"}
        }

        # Mock _get_request_from_storage
        request = make_request(path="/dashboard", cookies={"trading_session": "valid_cookie"})
        monkeypatch.setattr(middleware_module, "_get_request_from_storage", lambda: request)

        @middleware_module.requires_auth
        async def protected_page() -> str:
            return "success"

        with patch("apps.web_console_ng.auth.cookie_config.CookieConfig") as mock_cc:
            mock_cc.from_env.return_value = mock_cookie_cfg
            with patch(
                "apps.web_console_ng.auth.middleware.get_session_store",
                return_value=mock_session_store,
            ):
                result = await protected_page()

        assert result == "success"
        assert storage_data.get("logged_in") is True
        assert storage_data.get("user") == {"username": "test_user", "role": "admin"}
        assert storage_data.get("session_id") == "valid_cookie"
        assert "last_validated_at" in storage_data

    @pytest.mark.asyncio()
    async def test_requires_auth_returns_early_when_should_return(
        self, make_request: callable, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Test @requires_auth returns early when validation indicates."""
        storage_data: dict[str, Any] = {}
        storage_obj = MagicMock()
        storage_obj.get = lambda k, default=None: storage_data.get(k, default)
        storage_obj.__setitem__ = lambda self, k, v: storage_data.__setitem__(k, v)
        dummy_app = SimpleNamespace(storage=SimpleNamespace(user=storage_obj))
        monkeypatch.setattr(middleware_module, "app", dummy_app)
        monkeypatch.setattr(middleware_module.config, "AUTH_TYPE", "cookie")
        monkeypatch.setattr(middleware_module.config, "TRUSTED_PROXY_IPS", [])

        mock_cookie_cfg = MagicMock()
        mock_cookie_cfg.get_cookie_name.return_value = "trading_session"

        mock_ui = MagicMock()
        mock_ui.navigate = MagicMock()
        mock_ui.navigate.to = MagicMock()
        monkeypatch.setattr(middleware_module, "ui", mock_ui)

        # Mock _get_request_from_storage - no cookie
        request = make_request(path="/dashboard")
        monkeypatch.setattr(middleware_module, "_get_request_from_storage", lambda: request)

        @middleware_module.requires_auth
        async def protected_page() -> str:
            return "success"

        with patch("apps.web_console_ng.auth.cookie_config.CookieConfig") as mock_cc:
            mock_cc.from_env.return_value = mock_cookie_cfg
            result = await protected_page()

        # Should return None (early return)
        assert result is None
        mock_ui.navigate.to.assert_called_once_with("/login")


class TestRequiresRole:
    """Tests for @requires_role decorator."""

    @pytest.mark.asyncio()
    async def test_requires_role_allows_matching_role(
        self, make_request: callable, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Test @requires_role allows function execution for matching role."""
        storage_data: dict[str, Any] = {}
        storage_obj = MagicMock()
        storage_obj.get = lambda k, default=None: storage_data.get(k, default)
        storage_obj.__setitem__ = lambda self, k, v: storage_data.__setitem__(k, v)
        dummy_app = SimpleNamespace(storage=SimpleNamespace(user=storage_obj))
        monkeypatch.setattr(middleware_module, "app", dummy_app)
        monkeypatch.setattr(middleware_module.config, "AUTH_TYPE", "cookie")
        monkeypatch.setattr(middleware_module.config, "TRUSTED_PROXY_IPS", [])

        mock_cookie_cfg = MagicMock()
        mock_cookie_cfg.get_cookie_name.return_value = "trading_session"

        mock_session_store = AsyncMock()
        mock_session_store.validate_session.return_value = {
            "user": {"username": "admin_user", "role": "admin"}
        }

        request = make_request(path="/admin", cookies={"trading_session": "valid_cookie"})
        monkeypatch.setattr(middleware_module, "_get_request_from_storage", lambda: request)

        @middleware_module.requires_role("admin")
        async def admin_page() -> str:
            return "admin_success"

        with patch("apps.web_console_ng.auth.cookie_config.CookieConfig") as mock_cc:
            mock_cc.from_env.return_value = mock_cookie_cfg
            with patch(
                "apps.web_console_ng.auth.middleware.get_session_store",
                return_value=mock_session_store,
            ):
                result = await admin_page()

        assert result == "admin_success"
        assert storage_data.get("logged_in") is True

    @pytest.mark.asyncio()
    async def test_requires_role_redirects_for_wrong_role(
        self, make_request: callable, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Test @requires_role redirects to / for users with wrong role."""
        storage_data: dict[str, Any] = {}
        storage_obj = MagicMock()
        storage_obj.get = lambda k, default=None: storage_data.get(k, default)
        storage_obj.__setitem__ = lambda self, k, v: storage_data.__setitem__(k, v)
        dummy_app = SimpleNamespace(storage=SimpleNamespace(user=storage_obj))
        monkeypatch.setattr(middleware_module, "app", dummy_app)
        monkeypatch.setattr(middleware_module.config, "AUTH_TYPE", "cookie")
        monkeypatch.setattr(middleware_module.config, "TRUSTED_PROXY_IPS", [])

        mock_cookie_cfg = MagicMock()
        mock_cookie_cfg.get_cookie_name.return_value = "trading_session"

        mock_session_store = AsyncMock()
        mock_session_store.validate_session.return_value = {
            "user": {"username": "viewer_user", "role": "viewer"}  # Wrong role
        }

        mock_ui = MagicMock()
        mock_ui.navigate = MagicMock()
        mock_ui.navigate.to = MagicMock()
        monkeypatch.setattr(middleware_module, "ui", mock_ui)

        request = make_request(path="/admin", cookies={"trading_session": "valid_cookie"})
        monkeypatch.setattr(middleware_module, "_get_request_from_storage", lambda: request)

        @middleware_module.requires_role("admin")
        async def admin_page() -> str:
            return "admin_success"

        with patch("apps.web_console_ng.auth.cookie_config.CookieConfig") as mock_cc:
            mock_cc.from_env.return_value = mock_cookie_cfg
            with patch(
                "apps.web_console_ng.auth.middleware.get_session_store",
                return_value=mock_session_store,
            ):
                result = await admin_page()

        # Should return None (early return) and redirect to "/"
        assert result is None
        mock_ui.navigate.to.assert_called_once_with("/")

    @pytest.mark.asyncio()
    async def test_requires_role_returns_early_when_no_user(
        self, make_request: callable, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Test @requires_role returns early when no valid user."""
        storage_data: dict[str, Any] = {}
        storage_obj = MagicMock()
        storage_obj.get = lambda k, default=None: storage_data.get(k, default)
        storage_obj.__setitem__ = lambda self, k, v: storage_data.__setitem__(k, v)
        dummy_app = SimpleNamespace(storage=SimpleNamespace(user=storage_obj))
        monkeypatch.setattr(middleware_module, "app", dummy_app)
        monkeypatch.setattr(middleware_module.config, "AUTH_TYPE", "cookie")
        monkeypatch.setattr(middleware_module.config, "TRUSTED_PROXY_IPS", [])

        mock_cookie_cfg = MagicMock()
        mock_cookie_cfg.get_cookie_name.return_value = "trading_session"

        mock_ui = MagicMock()
        mock_ui.navigate = MagicMock()
        mock_ui.navigate.to = MagicMock()
        monkeypatch.setattr(middleware_module, "ui", mock_ui)

        request = make_request(path="/admin")  # No cookie
        monkeypatch.setattr(middleware_module, "_get_request_from_storage", lambda: request)

        @middleware_module.requires_role("admin")
        async def admin_page() -> str:
            return "admin_success"

        with patch("apps.web_console_ng.auth.cookie_config.CookieConfig") as mock_cc:
            mock_cc.from_env.return_value = mock_cookie_cfg
            result = await admin_page()

        assert result is None
        mock_ui.navigate.to.assert_called_once_with("/login")

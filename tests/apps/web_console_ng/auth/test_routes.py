from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace
from unittest.mock import AsyncMock
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from redis import exceptions as redis_exceptions
from starlette.datastructures import URL

from apps.web_console_ng.auth import routes
from apps.web_console_ng.auth.auth_result import AuthResult


@dataclass(frozen=True)
class _DummyCookieConfig:
    cookie_name: str = "test_session"

    def get_cookie_name(self) -> str:
        return self.cookie_name

    def get_cookie_flags(self) -> dict[str, object]:
        return {"httponly": True, "secure": False, "samesite": "lax", "path": "/"}

    def get_csrf_flags(self) -> dict[str, object]:
        return {"httponly": False, "secure": False, "samesite": "lax", "path": "/"}


class _DummyElement:
    def __init__(self, text: str | None = None) -> None:
        self.text = text

    def classes(self, _classes: str) -> _DummyElement:
        return self


@pytest.fixture()
def fastapi_app() -> FastAPI:
    app = FastAPI()
    app.include_router(routes.auth_api_router)
    return app


@pytest.fixture()
def cookie_config(monkeypatch: pytest.MonkeyPatch) -> _DummyCookieConfig:
    cfg = _DummyCookieConfig()
    monkeypatch.setattr(routes.CookieConfig, "from_env", lambda: cfg)
    return cfg


@pytest.fixture()
def ui_spy(monkeypatch: pytest.MonkeyPatch) -> dict[str, list[str]]:
    labels: list[str] = []
    navigations: list[str] = []

    def _label(text: str) -> _DummyElement:
        labels.append(text)
        return _DummyElement(text)

    def _button(_text: str, on_click=None) -> _DummyElement:  # noqa: ANN001
        return _DummyElement()

    def _navigate_to(path: str) -> None:
        navigations.append(path)

    monkeypatch.setattr(routes.ui, "label", _label)
    monkeypatch.setattr(routes.ui, "button", _button)
    monkeypatch.setattr(routes.ui.navigate, "to", _navigate_to)
    return {"labels": labels, "navigations": navigations}


@pytest.mark.asyncio()
async def test_login_post_missing_credentials_redirects(
    fastapi_app: FastAPI,
) -> None:
    async with AsyncClient(app=fastapi_app, base_url="http://test") as client:
        response = await client.post("/auth/login", data={})

    assert response.status_code == 303
    location = response.headers.get("location")
    assert location is not None
    parsed = urlparse(location)
    params = parse_qs(parsed.query)
    assert parsed.path == "/login"
    assert params["error"][0] == "Username and password required"


@pytest.mark.asyncio()
async def test_login_post_invalid_auth_type_redirects(
    fastapi_app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _raise(_auth_type: str):  # noqa: ANN001
        raise ValueError("Unknown auth type: nope")

    monkeypatch.setattr(routes, "get_auth_handler", _raise)

    async with AsyncClient(app=fastapi_app, base_url="http://test") as client:
        response = await client.post(
            "/auth/login",
            data={"username": "u", "password": "p", "auth_type": "nope", "next": "/"},
        )

    assert response.status_code == 303
    location = response.headers.get("location")
    assert location is not None
    parsed = urlparse(location)
    params = parse_qs(parsed.query)
    assert parsed.path == "/login"
    assert params["error"][0] == "Unknown auth type: nope"


@pytest.mark.asyncio()
async def test_login_post_success_sets_cookies_and_redirects(
    fastapi_app: FastAPI,
    cookie_config: _DummyCookieConfig,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    handler = SimpleNamespace(
        authenticate=AsyncMock(
            return_value=AuthResult(
                success=True,
                cookie_value="session-cookie",
                csrf_token="csrf-token",
                user_data={"user_id": "u1"},
            )
        )
    )
    monkeypatch.setattr(routes, "get_auth_handler", lambda _auth_type: handler)
    monkeypatch.setattr(routes, "extract_trusted_client_ip", lambda *_: "1.2.3.4")

    async with AsyncClient(app=fastapi_app, base_url="http://test") as client:
        response = await client.post(
            "/auth/login",
            data={"username": "u", "password": "p", "next": "/manual"},
        )

    assert response.status_code == 303
    assert response.headers.get("location") == "/manual"
    cookies = response.headers.get_list("set-cookie")
    assert any(cookie_config.cookie_name in header for header in cookies)
    assert any("ng_csrf" in header for header in cookies)


@pytest.mark.asyncio()
async def test_login_post_requires_mfa_sets_pending_cookie(
    fastapi_app: FastAPI,
    cookie_config: _DummyCookieConfig,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    handler = SimpleNamespace(
        authenticate=AsyncMock(
            return_value=AuthResult(
                success=True,
                requires_mfa=True,
                cookie_value="pending-cookie",
            )
        )
    )
    monkeypatch.setattr(routes, "get_auth_handler", lambda _auth_type: handler)
    monkeypatch.setattr(routes, "extract_trusted_client_ip", lambda *_: "1.2.3.4")

    async with AsyncClient(app=fastapi_app, base_url="http://test") as client:
        response = await client.post(
            "/auth/login",
            data={"username": "u", "password": "p", "next": "/manual"},
        )

    assert response.status_code == 303
    location = response.headers.get("location")
    assert location is not None
    assert location.startswith("/mfa-verify?")
    cookies = response.headers.get_list("set-cookie")
    assert any(cookie_config.cookie_name in header for header in cookies)
    assert all("ng_csrf" not in header for header in cookies)


@pytest.mark.asyncio()
async def test_login_post_rate_limited_message(
    fastapi_app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    handler = SimpleNamespace(
        authenticate=AsyncMock(
            return_value=AuthResult(
                success=False,
                rate_limited=True,
                retry_after=45,
            )
        )
    )
    monkeypatch.setattr(routes, "get_auth_handler", lambda _auth_type: handler)
    monkeypatch.setattr(routes, "extract_trusted_client_ip", lambda *_: "1.2.3.4")

    async with AsyncClient(app=fastapi_app, base_url="http://test") as client:
        response = await client.post(
            "/auth/login",
            data={"username": "u", "password": "p", "next": "/"},
        )

    assert response.status_code == 303
    location = response.headers.get("location")
    assert location is not None
    parsed = urlparse(location)
    params = parse_qs(parsed.query)
    assert parsed.path == "/login"
    assert "Too many attempts" in params["error"][0]
    assert "45" in params["error"][0]


@pytest.mark.asyncio()
async def test_login_post_sanitizes_redirect_path(
    fastapi_app: FastAPI,
    cookie_config: _DummyCookieConfig,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    handler = SimpleNamespace(
        authenticate=AsyncMock(
            return_value=AuthResult(success=True, cookie_value="cookie", csrf_token="csrf")
        )
    )
    monkeypatch.setattr(routes, "get_auth_handler", lambda _auth_type: handler)
    monkeypatch.setattr(routes, "extract_trusted_client_ip", lambda *_: "1.2.3.4")

    async with AsyncClient(app=fastapi_app, base_url="http://test") as client:
        response = await client.post(
            "/auth/login",
            data={"username": "u", "password": "p", "next": "https://evil.test/"},
        )

    assert response.status_code == 303
    assert response.headers.get("location") == "/"


@pytest.mark.asyncio()
@pytest.mark.asyncio()
async def test_auth_callback_rate_limited_blocks(
    ui_spy: dict[str, list[str]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = SimpleNamespace(
        headers={},
        url=URL("http://testserver/auth/callback"),
        client=SimpleNamespace(host="1.2.3.4"),
    )
    mock_contextvar = SimpleNamespace(get=lambda: request)
    monkeypatch.setattr("nicegui.storage.request_contextvar", mock_contextvar)
    monkeypatch.setattr(routes, "extract_trusted_client_ip", lambda *_: "1.2.3.4")

    class _Limiter:
        async def check_and_increment_ip(self, _ip: str):  # noqa: ANN001
            return True, 30, "blocked"

    monkeypatch.setattr(routes, "AuthRateLimiter", _Limiter)

    handler = SimpleNamespace(handle_callback=AsyncMock())
    monkeypatch.setattr(routes, "get_auth_handler", lambda _auth_type: handler)

    await routes.auth_callback(code="abc", state="xyz")

    assert "Too Many Requests" in ui_spy["labels"]
    handler.handle_callback.assert_not_called()


@pytest.mark.asyncio()
async def test_auth_callback_rate_limiter_redis_error(
    ui_spy: dict[str, list[str]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = SimpleNamespace(
        headers={},
        url=URL("http://testserver/auth/callback"),
        client=SimpleNamespace(host="1.2.3.4"),
    )
    mock_contextvar = SimpleNamespace(get=lambda: request)
    monkeypatch.setattr("nicegui.storage.request_contextvar", mock_contextvar)
    monkeypatch.setattr(routes, "extract_trusted_client_ip", lambda *_: "1.2.3.4")

    class _Limiter:
        async def check_and_increment_ip(self, _ip: str):  # noqa: ANN001
            raise redis_exceptions.RedisError("boom")

    monkeypatch.setattr(routes, "AuthRateLimiter", _Limiter)

    await routes.auth_callback(code="abc", state="xyz")

    assert "Service Temporarily Unavailable" in ui_spy["labels"]


@pytest.mark.asyncio()
# Additional comprehensive tests for login_post error paths and edge cases


@pytest.mark.asyncio()
async def test_login_post_lockout_message(
    fastapi_app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test that locked out users receive appropriate error message."""
    handler = SimpleNamespace(
        authenticate=AsyncMock(
            return_value=AuthResult(
                success=False,
                locked_out=True,
                lockout_remaining=300,
            )
        )
    )
    monkeypatch.setattr(routes, "get_auth_handler", lambda _auth_type: handler)
    monkeypatch.setattr(routes, "extract_trusted_client_ip", lambda *_: "1.2.3.4")

    async with AsyncClient(app=fastapi_app, base_url="http://test") as client:
        response = await client.post(
            "/auth/login",
            data={"username": "u", "password": "p", "next": "/"},
        )

    assert response.status_code == 303
    location = response.headers.get("location")
    assert location is not None
    parsed = urlparse(location)
    params = parse_qs(parsed.query)
    assert parsed.path == "/login"
    assert "Account locked" in params["error"][0]
    assert "300" in params["error"][0]


@pytest.mark.asyncio()
async def test_login_post_generic_failure_message(
    fastapi_app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test generic failure when no specific error message is provided."""
    handler = SimpleNamespace(
        authenticate=AsyncMock(
            return_value=AuthResult(
                success=False,
                error_message=None,
            )
        )
    )
    monkeypatch.setattr(routes, "get_auth_handler", lambda _auth_type: handler)
    monkeypatch.setattr(routes, "extract_trusted_client_ip", lambda *_: "1.2.3.4")

    async with AsyncClient(app=fastapi_app, base_url="http://test") as client:
        response = await client.post(
            "/auth/login",
            data={"username": "u", "password": "p", "next": "/"},
        )

    assert response.status_code == 303
    location = response.headers.get("location")
    assert location is not None
    parsed = urlparse(location)
    params = parse_qs(parsed.query)
    assert parsed.path == "/login"
    assert params["error"][0] == "Login failed"


@pytest.mark.asyncio()
async def test_login_post_custom_error_message(
    fastapi_app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test that custom error messages are propagated."""
    handler = SimpleNamespace(
        authenticate=AsyncMock(
            return_value=AuthResult(
                success=False,
                error_message="Invalid credentials provided",
            )
        )
    )
    monkeypatch.setattr(routes, "get_auth_handler", lambda _auth_type: handler)
    monkeypatch.setattr(routes, "extract_trusted_client_ip", lambda *_: "1.2.3.4")

    async with AsyncClient(app=fastapi_app, base_url="http://test") as client:
        response = await client.post(
            "/auth/login",
            data={"username": "u", "password": "p", "next": "/"},
        )

    assert response.status_code == 303
    location = response.headers.get("location")
    assert location is not None
    parsed = urlparse(location)
    params = parse_qs(parsed.query)
    assert parsed.path == "/login"
    assert params["error"][0] == "Invalid credentials provided"


@pytest.mark.asyncio()
async def test_login_post_missing_username_only(
    fastapi_app: FastAPI,
) -> None:
    """Test login with password but no username."""
    async with AsyncClient(app=fastapi_app, base_url="http://test") as client:
        response = await client.post("/auth/login", data={"password": "p"})

    assert response.status_code == 303
    location = response.headers.get("location")
    assert location is not None
    parsed = urlparse(location)
    params = parse_qs(parsed.query)
    assert parsed.path == "/login"
    assert params["error"][0] == "Username and password required"


@pytest.mark.asyncio()
async def test_login_post_missing_password_only(
    fastapi_app: FastAPI,
) -> None:
    """Test login with username but no password."""
    async with AsyncClient(app=fastapi_app, base_url="http://test") as client:
        response = await client.post("/auth/login", data={"username": "u"})

    assert response.status_code == 303
    location = response.headers.get("location")
    assert location is not None
    parsed = urlparse(location)
    params = parse_qs(parsed.query)
    assert parsed.path == "/login"
    assert params["error"][0] == "Username and password required"


@pytest.mark.asyncio()
async def test_login_post_success_without_csrf_token(
    fastapi_app: FastAPI,
    cookie_config: _DummyCookieConfig,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test login success when auth handler doesn't return CSRF token."""
    handler = SimpleNamespace(
        authenticate=AsyncMock(
            return_value=AuthResult(
                success=True,
                cookie_value="session-cookie",
                csrf_token=None,
                user_data={"user_id": "u1"},
            )
        )
    )
    monkeypatch.setattr(routes, "get_auth_handler", lambda _auth_type: handler)
    monkeypatch.setattr(routes, "extract_trusted_client_ip", lambda *_: "1.2.3.4")

    async with AsyncClient(app=fastapi_app, base_url="http://test") as client:
        response = await client.post(
            "/auth/login",
            data={"username": "u", "password": "p", "next": "/"},
        )

    assert response.status_code == 303
    assert response.headers.get("location") == "/"
    cookies = response.headers.get_list("set-cookie")
    assert any(cookie_config.cookie_name in header for header in cookies)
    assert all("ng_csrf" not in header for header in cookies)


@pytest.mark.asyncio()
async def test_login_post_success_without_cookie_value(
    fastapi_app: FastAPI,
    cookie_config: _DummyCookieConfig,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test login success when auth handler doesn't return cookie value."""
    handler = SimpleNamespace(
        authenticate=AsyncMock(
            return_value=AuthResult(
                success=True,
                cookie_value=None,
                csrf_token="csrf-token",
                user_data={"user_id": "u1"},
            )
        )
    )
    monkeypatch.setattr(routes, "get_auth_handler", lambda _auth_type: handler)
    monkeypatch.setattr(routes, "extract_trusted_client_ip", lambda *_: "1.2.3.4")

    async with AsyncClient(app=fastapi_app, base_url="http://test") as client:
        response = await client.post(
            "/auth/login",
            data={"username": "u", "password": "p", "next": "/"},
        )

    assert response.status_code == 303
    assert response.headers.get("location") == "/"
    cookies = response.headers.get_list("set-cookie")
    assert all(cookie_config.cookie_name not in header for header in cookies)
    assert any("ng_csrf" in header for header in cookies)


@pytest.mark.asyncio()
async def test_login_post_mfa_without_cookie_value(
    fastapi_app: FastAPI,
    cookie_config: _DummyCookieConfig,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test MFA flow when auth handler doesn't return cookie value."""
    handler = SimpleNamespace(
        authenticate=AsyncMock(
            return_value=AuthResult(
                success=True,
                requires_mfa=True,
                cookie_value=None,
            )
        )
    )
    monkeypatch.setattr(routes, "get_auth_handler", lambda _auth_type: handler)
    monkeypatch.setattr(routes, "extract_trusted_client_ip", lambda *_: "1.2.3.4")

    async with AsyncClient(app=fastapi_app, base_url="http://test") as client:
        response = await client.post(
            "/auth/login",
            data={"username": "u", "password": "p", "next": "/manual"},
        )

    assert response.status_code == 303
    location = response.headers.get("location")
    assert location is not None
    assert location.startswith("/mfa-verify?")
    cookies = response.headers.get_list("set-cookie")
    assert all(cookie_config.cookie_name not in header for header in cookies)


@pytest.mark.asyncio()
async def test_login_post_mfa_sanitizes_redirect(
    fastapi_app: FastAPI,
    cookie_config: _DummyCookieConfig,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test that MFA flow sanitizes redirect path."""
    handler = SimpleNamespace(
        authenticate=AsyncMock(
            return_value=AuthResult(
                success=True,
                requires_mfa=True,
                cookie_value="pending-cookie",
            )
        )
    )
    monkeypatch.setattr(routes, "get_auth_handler", lambda _auth_type: handler)
    monkeypatch.setattr(routes, "extract_trusted_client_ip", lambda *_: "1.2.3.4")

    async with AsyncClient(app=fastapi_app, base_url="http://test") as client:
        response = await client.post(
            "/auth/login",
            data={"username": "u", "password": "p", "next": "https://evil.test/"},
        )

    assert response.status_code == 303
    location = response.headers.get("location")
    assert location is not None
    assert location.startswith("/mfa-verify?")
    assert "next=%2F" in location or "next=/" in location

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
from starlette.responses import Response

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
    assert "/login" in location
    assert "Unknown auth type: nope" in location


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
    assert "Too many attempts" in location


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
async def test_auth_callback_success_sets_storage_and_cookies(
    cookie_config: _DummyCookieConfig,
    ui_spy: dict[str, list[str]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    response = Response()
    request = SimpleNamespace(
        headers={"user-agent": "ua"},
        url=URL("http://testserver/auth/callback?code=abc&state=xyz"),
        state=SimpleNamespace(response=response),
    )

    monkeypatch.setattr("nicegui.storage.request_contextvar.get", lambda: request)
    monkeypatch.setattr(routes, "extract_trusted_client_ip", lambda *_: "1.2.3.4")

    class _Limiter:
        async def check_and_increment_ip(self, _ip: str):  # noqa: ANN001
            return False, 0, "allowed"

    monkeypatch.setattr(routes, "AuthRateLimiter", _Limiter)

    handler = SimpleNamespace(
        handle_callback=AsyncMock(
            return_value=AuthResult(
                success=True,
                cookie_value="session-cookie",
                csrf_token="csrf-token",
                user_data={"user_id": "u1"},
            )
        )
    )
    monkeypatch.setattr(routes, "get_auth_handler", lambda _auth_type: handler)

    routes.app.storage.user = {"redirect_after_login": "/manual"}

    await routes.auth_callback(code="abc", state="xyz")

    cookies = response.headers.get_list("set-cookie")
    assert any(cookie_config.cookie_name in header for header in cookies)
    assert any("ng_csrf" in header for header in cookies)
    assert routes.app.storage.user["logged_in"] is True
    assert routes.app.storage.user["user"] == {"user_id": "u1"}
    assert routes.app.storage.user["session_id"] == "session-cookie"
    assert routes.app.storage.user.get("redirect_after_login") is None
    assert ui_spy["navigations"] == ["/manual"]


@pytest.mark.asyncio()
async def test_auth_callback_rate_limited_blocks(
    ui_spy: dict[str, list[str]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request = SimpleNamespace(headers={}, url=URL("http://testserver/auth/callback"))
    monkeypatch.setattr("nicegui.storage.request_contextvar.get", lambda: request)
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
    request = SimpleNamespace(headers={}, url=URL("http://testserver/auth/callback"))
    monkeypatch.setattr("nicegui.storage.request_contextvar.get", lambda: request)
    monkeypatch.setattr(routes, "extract_trusted_client_ip", lambda *_: "1.2.3.4")

    class _Limiter:
        async def check_and_increment_ip(self, _ip: str):  # noqa: ANN001
            raise redis_exceptions.RedisError("boom")

    monkeypatch.setattr(routes, "AuthRateLimiter", _Limiter)

    await routes.auth_callback(code="abc", state="xyz")

    assert "Service Temporarily Unavailable" in ui_spy["labels"]


@pytest.mark.asyncio()
async def test_auth_callback_missing_request_context(
    ui_spy: dict[str, list[str]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _missing():
        raise LookupError

    monkeypatch.setattr("nicegui.storage.request_contextvar.get", _missing)
    monkeypatch.setattr(routes.ui.context, "client", None, raising=False)

    await routes.auth_callback(code="abc", state="xyz")

    assert "Error: No request context" in ui_spy["labels"]

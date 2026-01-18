"""Unit tests for auth_service callback route."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from apps.auth_service.routes import callback as callback_module


@pytest.fixture()
def client() -> TestClient:
    """FastAPI app with only callback router registered."""
    app = FastAPI()
    app.include_router(callback_module.router)
    return TestClient(app)


def test_callback_uses_trusted_proxy_x_forwarded_for(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When proxy is trusted, callback should use X-Forwarded-For for client IP."""
    monkeypatch.setenv("TRUSTED_PROXY_IPS", "testclient")

    rate_limiter = AsyncMock()
    rate_limiter.is_allowed = AsyncMock(return_value=True)

    session_data = SimpleNamespace(user_id="auth0|user")
    handler = MagicMock()
    handler.handle_callback = AsyncMock(return_value=("session_123", session_data))

    config = SimpleNamespace(cookie_domain=None)

    with (
        patch(
            "apps.auth_service.routes.callback.get_rate_limiters",
            return_value={"callback": rate_limiter},
        ),
        patch("apps.auth_service.routes.callback.get_oauth2_handler", return_value=handler),
        patch("apps.auth_service.routes.callback.get_config", return_value=config),
    ):
        response = client.get(
            "/callback?code=abc&state=state123",
            headers={
                "X-Forwarded-For": "203.0.113.10, 10.0.0.1",
                "User-Agent": "unit-test-agent",
            },
            follow_redirects=False,
        )

    assert response.status_code == 302
    assert response.headers["location"] == "/"

    handler.handle_callback.assert_called_once_with(
        code="abc",
        state="state123",
        ip_address="203.0.113.10",
        user_agent="unit-test-agent",
    )


def test_callback_sets_cookie_domain(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """Callback should set cookie domain from config."""
    monkeypatch.setenv("TRUSTED_PROXY_IPS", "")

    rate_limiter = AsyncMock()
    rate_limiter.is_allowed = AsyncMock(return_value=True)

    session_data = SimpleNamespace(user_id="auth0|user")
    handler = MagicMock()
    handler.handle_callback = AsyncMock(return_value=("session_456", session_data))

    config = SimpleNamespace(cookie_domain=".example.test")

    with (
        patch(
            "apps.auth_service.routes.callback.get_rate_limiters",
            return_value={"callback": rate_limiter},
        ),
        patch("apps.auth_service.routes.callback.get_oauth2_handler", return_value=handler),
        patch("apps.auth_service.routes.callback.get_config", return_value=config),
    ):
        response = client.get(
            "/callback?code=code&state=state",
            headers={"User-Agent": "unit-test-agent"},
            follow_redirects=False,
        )

    assert response.status_code == 302
    set_cookie = response.headers.get("set-cookie", "")
    assert "session_id=session_456" in set_cookie
    assert "Domain=.example.test" in set_cookie


def test_callback_rate_limited_returns_429(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """Rate limited requests should return 429 without invoking handler."""
    monkeypatch.setenv("TRUSTED_PROXY_IPS", "")

    rate_limiter = AsyncMock()
    rate_limiter.is_allowed = AsyncMock(return_value=False)

    handler = MagicMock()
    handler.handle_callback = AsyncMock()

    config = SimpleNamespace(cookie_domain=None)

    with (
        patch(
            "apps.auth_service.routes.callback.get_rate_limiters",
            return_value={"callback": rate_limiter},
        ),
        patch("apps.auth_service.routes.callback.get_oauth2_handler", return_value=handler),
        patch("apps.auth_service.routes.callback.get_config", return_value=config),
    ):
        response = client.get(
            "/callback?code=code&state=state",
            headers={"User-Agent": "unit-test-agent"},
            follow_redirects=False,
        )

    assert response.status_code == 429
    handler.handle_callback.assert_not_called()

"""Unit tests for auth_service logout route."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from apps.auth_service.routes import logout as logout_module


@pytest.fixture()
def client() -> TestClient:
    """FastAPI app with only logout router registered."""
    app = FastAPI()
    app.include_router(logout_module.router)
    return TestClient(app)


def test_logout_without_session_redirects_to_login(client: TestClient) -> None:
    """Requests without session cookie should redirect to /login."""
    handler = MagicMock()
    handler.handle_logout = AsyncMock()

    with patch("apps.auth_service.routes.logout.get_oauth2_handler", return_value=handler):
        response = client.get("/logout", follow_redirects=False)

    assert response.status_code == 302
    assert response.headers["location"] == "/login"
    handler.handle_logout.assert_not_called()


def test_logout_clears_cookie_and_revokes_session(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Logout should validate binding, revoke session, and clear cookie."""
    monkeypatch.setenv("TRUSTED_PROXY_IPS", "testclient")

    handler = MagicMock()
    handler.handle_logout = AsyncMock(return_value="https://auth0.test/logout")
    config = SimpleNamespace(cookie_domain=".example.test")

    with (
        patch("apps.auth_service.routes.logout.get_oauth2_handler", return_value=handler),
        patch("apps.auth_service.routes.logout.get_config", return_value=config),
    ):
        response = client.get(
            "/logout",
            cookies={"session_id": "session_123"},
            headers={
                "X-Forwarded-For": "203.0.113.10, 10.0.0.1",
                "User-Agent": "unit-test-agent",
            },
            follow_redirects=False,
        )

    assert response.status_code == 302
    assert response.headers["location"] == "https://auth0.test/logout"

    handler.handle_logout.assert_called_once_with(
        "session_123",
        current_ip="203.0.113.10",
        current_user_agent="unit-test-agent",
    )

    set_cookie = response.headers.get("set-cookie", "")
    assert "session_id=" in set_cookie
    assert "Max-Age=0" in set_cookie
    assert "Domain=.example.test" in set_cookie

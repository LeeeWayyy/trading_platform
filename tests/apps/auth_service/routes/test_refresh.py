"""Unit tests for auth_service refresh route."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from apps.auth_service.routes import refresh as refresh_module


@pytest.fixture()
def client() -> TestClient:
    """FastAPI app with only refresh router registered."""
    app = FastAPI()
    app.include_router(refresh_module.router)
    return TestClient(app)


def test_refresh_requires_session_cookie(client: TestClient) -> None:
    """Missing session cookie should return 401."""
    response = client.post("/refresh")

    assert response.status_code == 401
    assert response.json()["detail"] == "No session cookie"


def test_refresh_rate_limited_returns_429(client: TestClient) -> None:
    """Rate limited requests should return 429 without invoking handler."""
    rate_limiter = AsyncMock()
    rate_limiter.is_allowed = AsyncMock(return_value=False)

    handler = MagicMock()
    handler.refresh_tokens = AsyncMock()

    with (
        patch(
            "apps.auth_service.routes.refresh.get_rate_limiters",
            return_value={"refresh": rate_limiter},
        ),
        patch("apps.auth_service.routes.refresh.get_oauth2_handler", return_value=handler),
    ):
        response = client.post("/refresh", cookies={"session_id": "session_123"})

    assert response.status_code == 429
    handler.refresh_tokens.assert_not_called()


def test_refresh_uses_binding_validation_for_standard_requests(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Standard refresh enforces binding validation with trusted proxy IP."""
    monkeypatch.setenv("TRUSTED_PROXY_IPS", "testclient")
    monkeypatch.setattr(refresh_module, "INTERNAL_REFRESH_SECRET", None)

    rate_limiter = AsyncMock()
    rate_limiter.is_allowed = AsyncMock(return_value=True)

    session_data = SimpleNamespace(user_id="auth0|user")
    handler = MagicMock()
    handler.refresh_tokens = AsyncMock(return_value=session_data)

    with (
        patch(
            "apps.auth_service.routes.refresh.get_rate_limiters",
            return_value={"refresh": rate_limiter},
        ),
        patch("apps.auth_service.routes.refresh.get_oauth2_handler", return_value=handler),
    ):
        response = client.post(
            "/refresh",
            cookies={"session_id": "session_123"},
            headers={
                "X-Forwarded-For": "203.0.113.10, 10.0.0.1",
                "User-Agent": "unit-test-agent",
            },
        )

    assert response.status_code == 200
    assert response.json()["status"] == "success"

    handler.refresh_tokens.assert_called_once_with(
        session_id="session_123",
        ip_address="203.0.113.10",
        user_agent="unit-test-agent",
    )


def test_refresh_internal_bypass_skips_binding(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Internal refresh should skip binding when shared secret matches."""
    monkeypatch.setattr(refresh_module, "INTERNAL_REFRESH_SECRET", "shared-secret")

    rate_limiter = AsyncMock()
    rate_limiter.is_allowed = AsyncMock(return_value=True)

    session_data = SimpleNamespace(user_id="auth0|user")
    handler = MagicMock()
    handler.refresh_tokens = AsyncMock(return_value=session_data)

    with (
        patch(
            "apps.auth_service.routes.refresh.get_rate_limiters",
            return_value={"refresh": rate_limiter},
        ),
        patch("apps.auth_service.routes.refresh.get_oauth2_handler", return_value=handler),
    ):
        response = client.post(
            "/refresh",
            cookies={"session_id": "session_123"},
            headers={"X-Internal-Auth": "shared-secret"},
        )

    assert response.status_code == 200
    assert response.json()["status"] == "success"

    handler.refresh_tokens.assert_called_once_with(
        session_id="session_123",
        ip_address=None,
        user_agent=None,
        enforce_binding=False,
    )


def test_refresh_invalid_internal_header_returns_401(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Invalid internal auth header should be rejected when secret is configured."""
    monkeypatch.setattr(refresh_module, "INTERNAL_REFRESH_SECRET", "shared-secret")

    rate_limiter = AsyncMock()
    rate_limiter.is_allowed = AsyncMock(return_value=True)

    handler = MagicMock()
    handler.refresh_tokens = AsyncMock()

    with (
        patch(
            "apps.auth_service.routes.refresh.get_rate_limiters",
            return_value={"refresh": rate_limiter},
        ),
        patch("apps.auth_service.routes.refresh.get_oauth2_handler", return_value=handler),
    ):
        response = client.post(
            "/refresh",
            cookies={"session_id": "session_123"},
            headers={"X-Internal-Auth": "wrong-secret"},
        )

    assert response.status_code == 401
    assert response.json()["detail"] == "Invalid internal auth header"
    handler.refresh_tokens.assert_not_called()

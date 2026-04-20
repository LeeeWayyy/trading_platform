"""Unit tests for auth_service refresh route."""

from __future__ import annotations

import importlib
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
    # TestClient sets request.client to None, so get_remote_addr() returns "unknown"
    # Set trusted proxy to "unknown" to simulate trusted proxy in test environment
    monkeypatch.setenv("TRUSTED_PROXY_IPS", "unknown")
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


def test_internal_refresh_secret_env_is_stripped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression for #176: whitespace-padded env var must be stripped.

    ``main._validate_internal_refresh_secret`` strips the env var during
    startup validation; the refresh route must do the same so
    ``secrets.compare_digest`` succeeds when callers send the intended
    (unstripped) secret.
    """
    monkeypatch.setenv("INTERNAL_REFRESH_SECRET", "  shared-secret  ")

    reloaded = importlib.reload(refresh_module)
    try:
        assert reloaded.INTERNAL_REFRESH_SECRET == "shared-secret"
    finally:
        # Restore the original environment (including any pre-existing
        # INTERNAL_REFRESH_SECRET value) before reloading so later tests see a
        # clean module state rather than the delenv-forced ``None``.
        monkeypatch.undo()
        importlib.reload(refresh_module)


def test_internal_refresh_secret_whitespace_only_becomes_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Whitespace-only env var must be treated as unset (disabled feature)."""
    monkeypatch.setenv("INTERNAL_REFRESH_SECRET", "   ")

    reloaded = importlib.reload(refresh_module)
    try:
        assert reloaded.INTERNAL_REFRESH_SECRET is None
    finally:
        monkeypatch.undo()
        importlib.reload(refresh_module)


def test_refresh_internal_bypass_succeeds_when_env_has_whitespace(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression for #176: whitespace in env must not break compare_digest.

    Simulates the production setup where ``INTERNAL_REFRESH_SECRET`` was
    configured with stray whitespace. The route should authenticate callers
    that send the intended (stripped) value in ``X-Internal-Auth``.
    """
    # Load the module with a padded env var so the module-level constant is
    # populated via the real ``os.getenv(...).strip()`` code path.
    monkeypatch.setenv("INTERNAL_REFRESH_SECRET", "  shared-secret  ")
    reloaded = importlib.reload(refresh_module)

    try:
        assert reloaded.INTERNAL_REFRESH_SECRET == "shared-secret"

        app = FastAPI()
        app.include_router(reloaded.router)
        local_client = TestClient(app)

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
            patch(
                "apps.auth_service.routes.refresh.get_oauth2_handler",
                return_value=handler,
            ),
        ):
            response = local_client.post(
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
    finally:
        monkeypatch.undo()
        importlib.reload(refresh_module)


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

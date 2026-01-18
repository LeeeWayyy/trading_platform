"""Unit tests for auth_service main module."""

import base64
import importlib
import pytest
from fastapi.testclient import TestClient

import apps.auth_service.dependencies as dependencies


def _reload_main(monkeypatch: pytest.MonkeyPatch, *, report_only: bool) -> object:
    """Reload main module with controlled environment."""
    monkeypatch.setenv("AUTH0_DOMAIN", "test.auth0.com")
    monkeypatch.setenv("CSP_REPORT_ONLY", "true" if report_only else "false")
    monkeypatch.setenv("TRUSTED_PROXY_IPS", "127.0.0.1")
    dependencies.get_config.cache_clear()

    import apps.auth_service.main as main

    return importlib.reload(main)


def test_http_exception_handler_adds_csp_header(monkeypatch: pytest.MonkeyPatch) -> None:
    """Missing routes should include CSP header from exception handler."""
    main = _reload_main(monkeypatch, report_only=False)
    client = TestClient(main.app)

    response = client.get("/missing-route")

    assert response.status_code == 404
    assert "Content-Security-Policy" in response.headers
    assert "Content-Security-Policy-Report-Only" not in response.headers
    assert "https://test.auth0.com" in response.headers["Content-Security-Policy"]


def test_http_exception_handler_report_only(monkeypatch: pytest.MonkeyPatch) -> None:
    """Report-only mode should emit CSP report-only header."""
    main = _reload_main(monkeypatch, report_only=True)
    client = TestClient(main.app)

    response = client.get("/missing-route")

    assert response.status_code == 404
    assert "Content-Security-Policy-Report-Only" in response.headers
    assert "Content-Security-Policy" not in response.headers


def test_validate_internal_refresh_secret_missing_prod_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Production should fail closed when INTERNAL_REFRESH_SECRET missing."""
    import apps.auth_service.main as main

    monkeypatch.setenv("ENVIRONMENT", "prod")
    monkeypatch.delenv("INTERNAL_REFRESH_SECRET", raising=False)

    with pytest.raises(RuntimeError, match="INTERNAL_REFRESH_SECRET not set"):
        main._validate_internal_refresh_secret()


def test_validate_internal_refresh_secret_short_prod_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Production should reject too-short secrets."""
    import apps.auth_service.main as main

    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("INTERNAL_REFRESH_SECRET", "short")

    with pytest.raises(RuntimeError, match="too short"):
        main._validate_internal_refresh_secret()


def test_validate_internal_refresh_secret_allows_dev_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Dev environment allows insecure defaults with warning."""
    import apps.auth_service.main as main

    monkeypatch.setenv("ENVIRONMENT", "dev")
    monkeypatch.setenv("INTERNAL_REFRESH_SECRET", "dev-internal-refresh-secret")

    assert main._validate_internal_refresh_secret() == "dev-internal-refresh-secret"


def test_validate_internal_refresh_secret_unset_dev_returns_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Dev environment allows unset secret and returns None."""
    import apps.auth_service.main as main

    monkeypatch.setenv("ENVIRONMENT", "dev")
    monkeypatch.delenv("INTERNAL_REFRESH_SECRET", raising=False)

    assert main._validate_internal_refresh_secret() is None


@pytest.fixture()
def valid_session_key_b64() -> str:
    """Provide a valid base64-encoded 32-byte key."""
    return base64.b64encode(b"0" * 32).decode("ascii")


def test_login_endpoint_uses_oauth2_handler(
    monkeypatch: pytest.MonkeyPatch, valid_session_key_b64: str
) -> None:
    """Smoke test /login endpoint uses handler for redirect."""
    from unittest.mock import AsyncMock, MagicMock

    monkeypatch.setenv("SESSION_ENCRYPTION_KEY", valid_session_key_b64)
    monkeypatch.setenv("AUTH0_DOMAIN", "test.auth0.com")

    dependencies.get_config.cache_clear()

    import apps.auth_service.main as main

    handler = MagicMock()
    handler.initiate_login = AsyncMock(
        return_value=("https://auth0.example/authorize", MagicMock(state="s"))
    )

    monkeypatch.setattr(main, "get_oauth2_handler", lambda: handler)

    client = TestClient(main.app)
    response = client.get("/login", follow_redirects=False)

    assert response.status_code == 302
    assert response.headers["location"].startswith("https://auth0.example/authorize")
    handler.initiate_login.assert_called_once()

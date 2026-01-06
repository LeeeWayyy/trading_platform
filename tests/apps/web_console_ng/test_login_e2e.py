"""End-to-end tests for login flow with proper cookie handling.

These tests simulate real browser behavior to verify:
1. POST /auth/login sets session cookies correctly
2. Cookies are sent on subsequent requests
3. Protected pages are accessible with valid cookies
4. Protected pages redirect to login without cookies
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

# Set test environment before importing app modules
os.environ.setdefault("WEB_CONSOLE_NG_DEBUG", "true")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/1")

if TYPE_CHECKING:
    pass


@pytest.fixture
def mock_session_store() -> AsyncMock:
    """Create a mock session store for testing."""
    store = AsyncMock()
    store.create_session.return_value = ("test_session_cookie_value", "test_csrf_token")
    store.validate_session.return_value = {
        "user": {
            "user_id": "test-user",
            "username": "admin",
            "role": "admin",
        }
    }
    return store


@pytest.fixture
def mock_audit_logger() -> AsyncMock:
    """Create a mock audit logger."""
    logger = AsyncMock()
    logger.log = AsyncMock()
    logger.start = AsyncMock()
    logger.stop = AsyncMock()
    return logger


class TestLoginE2EFlow:
    """Test the complete login flow simulating browser behavior.

    These tests use httpx.AsyncClient with ASGITransport to test the actual
    HTTP flow without needing a running server.
    """

    @pytest.mark.asyncio
    async def test_login_post_sets_cookies_and_redirects(
        self, mock_session_store: AsyncMock, mock_audit_logger: AsyncMock
    ) -> None:
        """Test that POST /auth/login sets session cookies and redirects."""
        # Import module to patch
        import apps.web_console_ng.auth.providers.dev as dev_module

        # Mock at the module level where get_session_store is called
        with patch.object(dev_module, "get_session_store", return_value=mock_session_store):
            # Import the router directly to test without full app initialization
            from apps.web_console_ng.auth.routes import auth_api_router
            from fastapi import FastAPI

            # Create minimal FastAPI app with just the auth router
            test_app = FastAPI()
            test_app.include_router(auth_api_router)

            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=test_app),  # type: ignore[arg-type]
                base_url="http://localhost:8080",
                follow_redirects=False,
            ) as client:
                response = await client.post(
                    "/auth/login",
                    data={
                        "username": "admin",
                        "password": "changeme",
                        "auth_type": "dev",
                        "next": "/",
                    },
                )

                # Should redirect after successful login
                assert response.status_code == 303, f"Expected 303, got {response.status_code}: {response.text}"
                assert response.headers.get("location") == "/"

                # Check cookies are set
                cookies = response.cookies
                # Cookie name depends on SESSION_COOKIE_SECURE (false in DEBUG mode)
                has_session_cookie = (
                    "nicegui_session" in cookies or "__Host-nicegui_session" in cookies
                )
                assert has_session_cookie, f"Session cookie not set. Cookies: {list(cookies.keys())}"
                assert "ng_csrf" in cookies, f"CSRF cookie not set. Cookies: {list(cookies.keys())}"

    @pytest.mark.asyncio
    async def test_full_login_flow_with_cookie_persistence(
        self, mock_session_store: AsyncMock, mock_audit_logger: AsyncMock
    ) -> None:
        """Test complete login flow: POST → get cookies → access protected page."""
        # Import modules to patch
        import apps.web_console_ng.auth.providers.dev as dev_module
        import apps.web_console_ng.auth.middleware as middleware_module

        with (
            patch.object(dev_module, "get_session_store", return_value=mock_session_store),
            patch.object(middleware_module, "get_session_store", return_value=mock_session_store),
        ):
            from apps.web_console_ng.auth.routes import auth_api_router
            from apps.web_console_ng.auth.middleware import AuthMiddleware
            from fastapi import FastAPI
            from starlette.responses import PlainTextResponse

            # Create test app with auth router and middleware
            test_app = FastAPI()
            test_app.include_router(auth_api_router)

            # Add a protected route
            @test_app.get("/")
            async def protected_route() -> PlainTextResponse:
                return PlainTextResponse("Dashboard")

            # Add AuthMiddleware
            test_app.add_middleware(AuthMiddleware)

            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=test_app),  # type: ignore[arg-type]
                base_url="http://localhost:8080",
                follow_redirects=False,
            ) as client:
                # Step 1: Try accessing protected page without cookies
                unauth_response = await client.get(
                    "/", headers={"Accept": "text/html"}
                )
                assert unauth_response.status_code == 302, (
                    f"Expected redirect to login, got {unauth_response.status_code}"
                )
                assert "/login" in unauth_response.headers.get("location", "")

                # Step 2: POST login credentials
                login_response = await client.post(
                    "/auth/login",
                    data={
                        "username": "admin",
                        "password": "changeme",
                        "auth_type": "dev",
                        "next": "/",
                    },
                )

                assert login_response.status_code == 303
                assert login_response.headers.get("location") == "/"

                # Step 3: Extract cookies from login response
                cookies = {}
                for cookie_name in ["nicegui_session", "__Host-nicegui_session", "ng_csrf"]:
                    if cookie_name in login_response.cookies:
                        cookies[cookie_name] = login_response.cookies[cookie_name]

                assert len(cookies) >= 1, f"No cookies set. Response cookies: {list(login_response.cookies.keys())}"

                # Step 4: Access protected page WITH cookies
                dashboard_response = await client.get(
                    "/",
                    headers={"Accept": "text/html"},
                    cookies=cookies,
                )

                # Should NOT redirect to login (200 OK for dashboard)
                assert dashboard_response.status_code == 200, (
                    f"Expected 200 OK but got {dashboard_response.status_code}. "
                    f"Location: {dashboard_response.headers.get('location', 'N/A')}. "
                    f"Cookies sent: {list(cookies.keys())}"
                )


class TestCookieConfiguration:
    """Test cookie configuration for different environments."""

    def test_debug_mode_uses_non_secure_cookies(self) -> None:
        """In DEBUG mode, cookies should NOT use Secure flag (for HTTP localhost)."""
        from apps.web_console_ng.auth.cookie_config import CookieConfig

        cookie_cfg = CookieConfig.from_env()

        # In DEBUG mode (set in conftest), secure should be False
        assert cookie_cfg.secure is False, (
            "DEBUG mode should disable Secure cookies for localhost development"
        )

        # Cookie name should NOT have __Host- prefix
        cookie_name = cookie_cfg.get_cookie_name()
        assert not cookie_name.startswith("__Host-"), (
            f"Cookie name '{cookie_name}' should not use __Host- prefix in DEBUG mode"
        )

    def test_cookie_flags_for_localhost(self) -> None:
        """Test cookie flags are compatible with localhost development."""
        from apps.web_console_ng.auth.cookie_config import CookieConfig

        cookie_cfg = CookieConfig.from_env()
        flags = cookie_cfg.get_cookie_flags()

        # Secure must be False for HTTP localhost
        assert flags["secure"] is False, "Secure flag must be False for HTTP localhost"

        # SameSite should be 'lax' for localhost (most compatible)
        assert flags["samesite"] in ("lax", "strict"), (
            f"SameSite={flags['samesite']} may cause issues on localhost"
        )


class TestSecureCookieIssue:
    """Regression tests for the Secure cookie issue on localhost."""

    def test_secure_cookie_causes_redirect_loop_on_http(self) -> None:
        """
        Document the issue: Secure cookies on HTTP localhost cause redirect loops.

        When SESSION_COOKIE_SECURE=true:
        1. Server sets cookie with Secure flag
        2. Browser accepts Set-Cookie header
        3. Browser does NOT send cookie back on HTTP requests
        4. Server sees no cookie → redirects to login
        5. Infinite loop

        This test verifies DEBUG mode prevents this issue.
        """
        from apps.web_console_ng import config
        from apps.web_console_ng.auth.cookie_config import CookieConfig

        # Verify DEBUG mode is enabled in tests
        assert config.DEBUG is True, "Tests must run with DEBUG=true"

        # Verify Secure is disabled in DEBUG mode
        assert config.SESSION_COOKIE_SECURE is False, (
            "SESSION_COOKIE_SECURE should be False in DEBUG mode"
        )

        # Verify cookie config reflects this
        cookie_cfg = CookieConfig.from_env()
        assert cookie_cfg.secure is False

        # Verify cookie name doesn't use __Host- prefix
        cookie_name = cookie_cfg.get_cookie_name()
        assert cookie_name == "nicegui_session", (
            f"Expected 'nicegui_session' but got '{cookie_name}'"
        )

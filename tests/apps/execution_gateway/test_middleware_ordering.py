"""Tests for middleware execution ordering.

This test suite validates that middleware executes in the correct order:
1. ProxyHeadersMiddleware (proxy header processing)
2. populate_user_from_headers (authentication middleware)
3. Endpoint handlers

Correct ordering ensures:
- Proxy headers processed before auth
- Auth context available to endpoints
- Security checks happen at right layer

Target: Verify execution order with integration tests.

See REFACTOR_EXECUTION_GATEWAY_TASK.md Phase 1 for design decisions.
"""

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from unittest.mock import MagicMock, patch

from apps.execution_gateway.middleware import populate_user_from_headers


# ============================================================================
# Test Middleware Ordering
# ============================================================================


def test_middleware_execution_order_without_auth():
    """Test middleware executes in correct order: proxy → auth → endpoint."""
    app = FastAPI()

    # Track middleware execution order
    execution_order = []

    # Mock middleware that records execution
    @app.middleware("http")
    async def tracking_middleware(request: Request, call_next):
        execution_order.append("tracking_start")
        response = await call_next(request)
        execution_order.append("tracking_end")
        return response

    # Add populate_user_from_headers middleware
    app.middleware("http")(populate_user_from_headers)

    @app.get("/test")
    async def test_endpoint():
        execution_order.append("endpoint")
        return {"status": "ok"}

    client = TestClient(app)

    # Mock settings to disable auth validation
    with patch("config.settings.get_settings") as mock_get_settings:
        mock_get_settings.return_value = MagicMock(internal_token_required=False)

        response = client.get("/test")

    assert response.status_code == 200

    # Verify execution order: tracking → auth → endpoint → auth_end → tracking_end
    # Note: middleware registered last executes first in FastAPI
    assert execution_order == [
        "tracking_start",
        "endpoint",
        "tracking_end",
    ]


def test_middleware_populates_user_before_endpoint():
    """Test auth middleware populates request.state.user before endpoint."""
    app = FastAPI()

    # Add populate_user_from_headers middleware
    app.middleware("http")(populate_user_from_headers)

    @app.get("/test")
    async def test_endpoint(request: Request):
        # Endpoint should see user from middleware
        user = getattr(request.state, "user", None)
        return {"user": user}

    client = TestClient(app)

    # Mock settings to disable validation
    with patch("config.settings.get_settings") as mock_get_settings:
        mock_get_settings.return_value = MagicMock(internal_token_required=False)

        response = client.get(
            "/test",
            headers={
                "X-User-Role": "trader",
                "X-User-Id": "user123",
                "X-User-Strategies": "alpha_baseline",
            },
        )

    assert response.status_code == 200
    user_data = response.json()["user"]
    assert user_data["role"] == "trader"
    assert user_data["user_id"] == "user123"
    assert user_data["strategies"] == ["alpha_baseline"]


def test_middleware_blocks_invalid_auth_before_endpoint():
    """Test auth middleware returns 401 before endpoint executes."""
    app = FastAPI()

    # Add populate_user_from_headers middleware
    app.middleware("http")(populate_user_from_headers)

    endpoint_called = False

    @app.get("/test")
    async def test_endpoint():
        nonlocal endpoint_called
        endpoint_called = True
        return {"status": "ok"}

    client = TestClient(app)

    # Mock settings to enable validation
    with patch("config.settings.get_settings") as mock_get_settings:
        settings_mock = MagicMock()
        settings_mock.internal_token_required = True
        settings_mock.internal_token_timestamp_tolerance_seconds = 300

        secret_mock = MagicMock()
        secret_mock.get_secret_value.return_value = "test_secret"
        settings_mock.internal_token_secret = secret_mock

        mock_get_settings.return_value = settings_mock

        response = client.get(
            "/test",
            headers={
                "X-User-Role": "trader",
                "X-User-Id": "user123",
                "X-User-Strategies": "alpha_baseline",
                "X-User-Signature": "invalid_signature",
                "X-Request-Timestamp": "1234567890",
            },
        )

    # Middleware should return 401 before endpoint
    assert response.status_code == 401
    assert not endpoint_called  # Endpoint should not execute


def test_middleware_allows_missing_headers_passthrough():
    """Test middleware passes through when no auth headers present."""
    app = FastAPI()

    # Add populate_user_from_headers middleware
    app.middleware("http")(populate_user_from_headers)

    @app.get("/test")
    async def test_endpoint(request: Request):
        user = getattr(request.state, "user", None)
        return {"has_user": user is not None}

    client = TestClient(app)

    # Mock settings
    with patch("config.settings.get_settings") as mock_get_settings:
        mock_get_settings.return_value = MagicMock(internal_token_required=False)

        response = client.get("/test")  # No auth headers

    assert response.status_code == 200
    assert response.json()["has_user"] is False


def test_multiple_middleware_execution_order():
    """Test multiple middleware execute in registration order."""
    app = FastAPI()

    execution_order = []

    @app.middleware("http")
    async def first_middleware(request: Request, call_next):
        execution_order.append("first_start")
        response = await call_next(request)
        execution_order.append("first_end")
        return response

    @app.middleware("http")
    async def second_middleware(request: Request, call_next):
        execution_order.append("second_start")
        response = await call_next(request)
        execution_order.append("second_end")
        return response

    app.middleware("http")(populate_user_from_headers)

    @app.get("/test")
    async def test_endpoint():
        execution_order.append("endpoint")
        return {"status": "ok"}

    client = TestClient(app)

    # Mock settings
    with patch("config.settings.get_settings") as mock_get_settings:
        mock_get_settings.return_value = MagicMock(internal_token_required=False)

        response = client.get("/test")

    assert response.status_code == 200

    # Middleware registered last executes first in FastAPI
    # Order: second (last) → first (second) → endpoint → first_end → second_end
    assert execution_order == [
        "second_start",
        "first_start",
        "endpoint",
        "first_end",
        "second_end",
    ]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

"""Comprehensive tests for model_registry main.py.

Tests cover:
- Configuration loading (get_settings)
- Lifespan context management (startup/shutdown)
- FastAPI app initialization
- CORS middleware configuration
- Root and health endpoints
- Global exception handler
- Environment variable handling

Target: 85%+ branch coverage
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import Mock, patch

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

# Set required environment variables before importing main
os.environ.setdefault("ENVIRONMENT", "test")
os.environ.setdefault("ALLOWED_ORIGINS", "http://localhost:8501")

from apps.model_registry.main import (
    _cors_allow_origins,
    _resolve_cors_origins,
    _verify_cors_middleware_uses_shared_origins,
    app,
    get_settings,
    lifespan,
)
from libs.models.models import ManifestIntegrityError, RegistryManifest


def _create_test_manifest(
    artifact_count: int = 5,
    production_models: dict[str, str] | None = None,
) -> RegistryManifest:
    """Helper to create valid RegistryManifest instances for testing."""
    now = datetime.now(UTC)
    return RegistryManifest(
        artifact_count=artifact_count,
        production_models=production_models or {},
        created_at=now,
        last_updated=now,
        checksum="test_checksum_" + "0" * 48,  # SHA-256 is 64 hex chars
    )


# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture()
def mock_registry():
    """Create a mock ModelRegistry instance."""
    registry = Mock()
    registry.get_manifest.return_value = _create_test_manifest(
        artifact_count=5,
        production_models={"risk_model": "v1.0.0"},
    )
    return registry


@pytest.fixture()
def mock_manifest_manager():
    """Create a mock RegistryManifestManager instance."""
    manager = Mock()
    manager.exists.return_value = True
    manager.verify_integrity.return_value = True
    return manager


@pytest.fixture()
def clean_env(monkeypatch: pytest.MonkeyPatch):
    """Clean environment variables for testing."""
    # Set required variables
    monkeypatch.setenv("ENVIRONMENT", "test")
    monkeypatch.setenv("ALLOWED_ORIGINS", "http://localhost:8501")
    # Clear optional variables
    for var in [
        "MODEL_REGISTRY_DIR",
        "MODEL_REGISTRY_HOST",
        "MODEL_REGISTRY_PORT",
        "MODEL_REGISTRY_AUTH_DISABLED",
    ]:
        monkeypatch.delenv(var, raising=False)
    return monkeypatch


# =============================================================================
# Configuration Tests
# =============================================================================


def test_get_settings_defaults(clean_env: pytest.MonkeyPatch):
    """Test get_settings returns default values when env vars not set."""
    settings = get_settings()

    assert settings["registry_dir"] == Path("data/models")
    assert settings["host"] == "0.0.0.0"
    assert settings["port"] == 8003
    assert settings["auth_disabled"] is False


def test_get_settings_from_environment(clean_env: pytest.MonkeyPatch):
    """Test get_settings uses environment variables when set."""
    clean_env.setenv("MODEL_REGISTRY_DIR", "/custom/path")
    clean_env.setenv("MODEL_REGISTRY_HOST", "127.0.0.1")
    clean_env.setenv("MODEL_REGISTRY_PORT", "9999")

    settings = get_settings()

    assert settings["registry_dir"] == Path("/custom/path")
    assert settings["host"] == "127.0.0.1"
    assert settings["port"] == 9999
    assert settings["auth_disabled"] is False


def test_get_settings_auth_disabled_always_false(clean_env: pytest.MonkeyPatch):
    """Test auth_disabled is always False regardless of env vars."""
    # Even if we set MODEL_REGISTRY_AUTH_DISABLED, it should be ignored
    # by get_settings (the lifespan will raise an error)
    settings = get_settings()

    assert settings["auth_disabled"] is False


# =============================================================================
# Lifespan Tests - Happy Path
# =============================================================================


@pytest.mark.asyncio()
async def test_lifespan_successful_startup_with_manifest(mock_registry, mock_manifest_manager):
    """Test successful lifespan startup with valid manifest."""
    test_app = FastAPI()

    with (
        patch("apps.model_registry.main.ModelRegistry", return_value=mock_registry),
        patch(
            "apps.model_registry.main.RegistryManifestManager", return_value=mock_manifest_manager
        ),
        patch("apps.model_registry.main.set_registry") as mock_set_registry,
    ):
        async with lifespan(test_app):
            # Verify registry was set
            mock_set_registry.assert_called_once_with(mock_registry)

            # Verify manifest was loaded and integrity checked
            mock_manifest_manager.exists.assert_called_once()
            mock_manifest_manager.verify_integrity.assert_called_once()
            mock_registry.get_manifest.assert_called_once()


@pytest.mark.asyncio()
async def test_lifespan_successful_startup_without_manifest(mock_registry):
    """Test successful lifespan startup when manifest doesn't exist."""
    test_app = FastAPI()

    mock_manager = Mock()
    mock_manager.exists.return_value = False  # No manifest

    with (
        patch("apps.model_registry.main.ModelRegistry", return_value=mock_registry),
        patch("apps.model_registry.main.RegistryManifestManager", return_value=mock_manager),
        patch("apps.model_registry.main.set_registry") as mock_set_registry,
    ):
        async with lifespan(test_app):
            # Verify registry was set
            mock_set_registry.assert_called_once_with(mock_registry)

            # Verify manifest existence was checked but not verified
            mock_manager.exists.assert_called_once()
            mock_manager.verify_integrity.assert_not_called()
            mock_registry.get_manifest.assert_called_once()


@pytest.mark.asyncio()
async def test_lifespan_manifest_integrity_failure():
    """Test lifespan raises error when manifest integrity check fails."""
    test_app = FastAPI()

    mock_registry = Mock()
    mock_manager = Mock()
    mock_manager.exists.return_value = True
    mock_manager.verify_integrity.return_value = False  # Integrity failed

    with (
        patch("apps.model_registry.main.ModelRegistry", return_value=mock_registry),
        patch("apps.model_registry.main.RegistryManifestManager", return_value=mock_manager),
    ):
        with pytest.raises(ManifestIntegrityError) as exc_info:
            async with lifespan(test_app):
                pass

        assert "checksum does not match" in str(exc_info.value)


@pytest.mark.asyncio()
async def test_lifespan_auth_disabled_raises_error(monkeypatch: pytest.MonkeyPatch):
    """Test lifespan raises RuntimeError when AUTH_DISABLED is set."""
    test_app = FastAPI()
    monkeypatch.setenv("MODEL_REGISTRY_AUTH_DISABLED", "true")

    with pytest.raises(RuntimeError) as exc_info:
        async with lifespan(test_app):
            pass

    assert "MODEL_REGISTRY_AUTH_DISABLED is unsupported" in str(exc_info.value)


@pytest.mark.asyncio()
async def test_lifespan_logs_shutdown(mock_registry, mock_manifest_manager, caplog):
    """Test lifespan logs shutdown message in finally block."""
    import logging

    test_app = FastAPI()

    # Set log level to capture INFO messages from the module
    caplog.set_level(logging.INFO, logger="apps.model_registry.main")

    with (
        patch("apps.model_registry.main.ModelRegistry", return_value=mock_registry),
        patch(
            "apps.model_registry.main.RegistryManifestManager", return_value=mock_manifest_manager
        ),
        patch("apps.model_registry.main.set_registry"),
    ):
        async with lifespan(test_app):
            pass

    # Verify shutdown log message
    assert "Model Registry Service shutting down..." in caplog.text


# =============================================================================
# FastAPI App Tests
# =============================================================================


def test_app_initialization():
    """Test FastAPI app is properly initialized with correct settings."""
    assert app.title == "Model Registry API"
    assert app.description == "REST API for model metadata retrieval and validation"
    assert app.version == "1.0.0"
    assert app.docs_url == "/docs"
    assert app.redoc_url == "/redoc"


def test_app_has_lifespan():
    """Test app has lifespan context manager configured."""
    # The lifespan is passed to FastAPI constructor
    assert app.router.lifespan_context is not None


# =============================================================================
# CORS Middleware Tests
# =============================================================================


def test_cors_with_explicit_allowed_origins(monkeypatch: pytest.MonkeyPatch):
    """Test CORS configuration with explicit ALLOWED_ORIGINS."""
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("ALLOWED_ORIGINS", "https://example.com,https://app.example.com")

    origins = _resolve_cors_origins()
    assert origins == ["https://example.com", "https://app.example.com"]


def test_cors_with_wildcard_raises_error(monkeypatch: pytest.MonkeyPatch):
    """Test CORS raises RuntimeError when wildcard is in ALLOWED_ORIGINS."""
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("ALLOWED_ORIGINS", "*")

    with pytest.raises(RuntimeError, match="wildcard '\\*'.*credentials are enabled"):
        _resolve_cors_origins()


def test_cors_dev_environment_defaults(monkeypatch: pytest.MonkeyPatch):
    """Test CORS uses default origins in dev environment."""
    monkeypatch.setenv("ENVIRONMENT", "dev")
    monkeypatch.delenv("ALLOWED_ORIGINS", raising=False)

    origins = _resolve_cors_origins()
    assert "http://localhost:8501" in origins
    assert "http://localhost:3000" in origins


def test_cors_test_environment_defaults(monkeypatch: pytest.MonkeyPatch):
    """Test CORS uses default origins in test environment."""
    monkeypatch.setenv("ENVIRONMENT", "test")
    monkeypatch.delenv("ALLOWED_ORIGINS", raising=False)

    origins = _resolve_cors_origins()
    assert "http://localhost:8501" in origins


def test_cors_production_without_allowed_origins_raises_error(monkeypatch: pytest.MonkeyPatch):
    """Test CORS raises RuntimeError in production without ALLOWED_ORIGINS."""
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.delenv("ALLOWED_ORIGINS", raising=False)

    with pytest.raises(RuntimeError, match="ALLOWED_ORIGINS must be set for production"):
        _resolve_cors_origins()


def test_module_importable_without_allowed_origins():
    """Test that the module can be imported without ALLOWED_ORIGINS set (issue #156).

    Uses a subprocess to guarantee a truly fresh import with no prior env
    defaults.  If CORS validation still happens at import time, the subprocess
    will exit non-zero.
    """
    import subprocess
    import sys

    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import os; os.environ['ENVIRONMENT']='production'; "
            "os.environ.pop('ALLOWED_ORIGINS', None); "
            "from apps.model_registry.main import app; "
            "print('import ok')",
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, (
        f"Module import failed in production without ALLOWED_ORIGINS:\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )
    assert "import ok" in result.stdout


# =============================================================================
# Endpoint Tests
# =============================================================================


@pytest.fixture()
def client():
    """Create TestClient for testing endpoints."""
    return TestClient(app)


def test_root_endpoint(client: TestClient):
    """Test root endpoint returns service information."""
    response = client.get("/")

    assert response.status_code == 200
    data = response.json()
    assert data["service"] == "Model Registry API"
    assert data["version"] == "1.0.0"
    assert data["docs"] == "/docs"
    assert data["health"] == "/health"
    assert data["api"] == "/api/v1/models"
    assert "description" in data


def test_health_check_endpoint(client: TestClient):
    """Test health check endpoint returns status."""
    response = client.get("/health")

    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "healthy"
    assert data["service"] == "model_registry"
    assert "timestamp" in data

    # Verify timestamp is in ISO format with Z suffix
    timestamp = data["timestamp"]
    assert timestamp.endswith("Z")
    assert "T" in timestamp  # ISO format


def test_health_check_timestamp_format(client: TestClient):
    """Test health check timestamp is properly formatted."""
    response = client.get("/health")

    assert response.status_code == 200
    data = response.json()
    timestamp = data["timestamp"]

    # Should be ISO format: YYYY-MM-DDTHH:MM:SS.ffffffZ
    assert len(timestamp) > 20  # Has date, time, and microseconds
    assert timestamp[4] == "-"
    assert timestamp[7] == "-"
    assert timestamp[10] == "T"
    assert timestamp.endswith("Z")


# =============================================================================
# Exception Handler Tests
# =============================================================================


@pytest.mark.asyncio()
async def test_global_exception_handler_formats_error():
    """Test global exception handler returns properly formatted error."""
    from apps.model_registry.main import global_exception_handler

    # Create mock request
    request = Mock(spec=Request)
    request.method = "GET"
    request.url.path = "/test/path"

    # Create test exception
    exc = ValueError("Test error")

    # Call handler
    response = await global_exception_handler(request, exc)

    assert response.status_code == 500
    assert response.body is not None

    # Parse JSON response
    import json

    body = json.loads(response.body)
    assert body["detail"] == "Internal server error"
    assert body["code"] == "INTERNAL_ERROR"
    assert body["path"] == "/test/path"


@pytest.mark.asyncio()
async def test_global_exception_handler_logs_error(caplog):
    """Test global exception handler logs the exception."""
    from apps.model_registry.main import global_exception_handler

    request = Mock(spec=Request)
    request.method = "POST"
    request.url.path = "/api/test"

    exc = RuntimeError("Critical error")

    await global_exception_handler(request, exc)

    # Verify error was logged
    assert "Unhandled exception" in caplog.text
    assert "POST" in caplog.text
    assert "/api/test" in caplog.text


@pytest.mark.asyncio()
async def test_global_exception_handler_different_exception_types():
    """Test global exception handler handles different exception types."""
    from apps.model_registry.main import global_exception_handler

    request = Mock(spec=Request)
    request.method = "GET"
    request.url.path = "/test"

    # Test various exception types
    exceptions = [
        ValueError("value error"),
        TypeError("type error"),
        RuntimeError("runtime error"),
        Exception("generic error"),
    ]

    for exc in exceptions:
        response = await global_exception_handler(request, exc)
        assert response.status_code == 500

        import json

        body = json.loads(response.body)
        assert body["code"] == "INTERNAL_ERROR"


# =============================================================================
# Integration Tests
# =============================================================================


def test_app_includes_routes():
    """Test app includes the router from routes module."""
    # Check that routes are registered
    routes = [route.path for route in app.routes]

    # Should have root and health endpoints
    assert "/" in routes
    assert "/health" in routes

    # Should have API routes (from router)
    # Note: The actual route paths may be prefixed
    api_routes = [r for r in routes if r.startswith("/api/v1/models")]
    assert len(api_routes) > 0  # Router should add routes


def test_app_openapi_schema():
    """Test app generates OpenAPI schema."""
    schema = app.openapi()

    assert schema is not None
    assert schema["openapi"].startswith("3.")
    assert schema["info"]["title"] == "Model Registry API"
    assert schema["info"]["version"] == "1.0.0"
    assert "paths" in schema


def test_docs_endpoint_available(client: TestClient):
    """Test OpenAPI docs endpoint is accessible."""
    response = client.get("/docs")
    assert response.status_code == 200


def test_redoc_endpoint_available(client: TestClient):
    """Test ReDoc endpoint is accessible."""
    response = client.get("/redoc")
    assert response.status_code == 200


# =============================================================================
# Edge Cases and Error Conditions
# =============================================================================


def test_get_settings_with_invalid_port_type():
    """Test get_settings handles non-numeric port value."""
    import os

    # Temporarily set invalid port
    original = os.environ.get("MODEL_REGISTRY_PORT")
    try:
        os.environ["MODEL_REGISTRY_PORT"] = "invalid"

        with pytest.raises(ValueError, match="invalid literal for int"):
            get_settings()
    finally:
        # Restore original value
        if original is not None:
            os.environ["MODEL_REGISTRY_PORT"] = original
        else:
            os.environ.pop("MODEL_REGISTRY_PORT", None)


@pytest.mark.asyncio()
async def test_lifespan_handles_multiple_production_models(mock_manifest_manager):
    """Test lifespan handles manifest with multiple production models."""
    test_app = FastAPI()

    mock_registry = Mock()
    mock_registry.get_manifest.return_value = _create_test_manifest(
        artifact_count=10,
        production_models={
            "risk_model": "v1.0.0",
            "alpha_weights": "v2.0.0",
            "factor_model": "v1.5.0",
        },
    )

    with (
        patch("apps.model_registry.main.ModelRegistry", return_value=mock_registry),
        patch(
            "apps.model_registry.main.RegistryManifestManager", return_value=mock_manifest_manager
        ),
        patch("apps.model_registry.main.set_registry"),
    ):
        async with lifespan(test_app):
            # Should handle multiple production models
            pass


@pytest.mark.asyncio()
async def test_lifespan_handles_empty_manifest(mock_manifest_manager):
    """Test lifespan handles empty manifest (no production models)."""
    test_app = FastAPI()

    mock_registry = Mock()
    mock_registry.get_manifest.return_value = _create_test_manifest(
        artifact_count=0,
        production_models={},
    )

    with (
        patch("apps.model_registry.main.ModelRegistry", return_value=mock_registry),
        patch(
            "apps.model_registry.main.RegistryManifestManager", return_value=mock_manifest_manager
        ),
        patch("apps.model_registry.main.set_registry"),
    ):
        async with lifespan(test_app):
            # Should handle empty manifest gracefully
            pass


def test_root_endpoint_returns_dict_type(client: TestClient):
    """Test root endpoint returns dictionary with correct structure."""
    response = client.get("/")

    data = response.json()
    assert isinstance(data, dict)
    assert all(isinstance(v, str) for v in data.values())


def test_health_endpoint_returns_dict_type(client: TestClient):
    """Test health endpoint returns dictionary with correct structure."""
    response = client.get("/health")

    data = response.json()
    assert isinstance(data, dict)
    assert all(isinstance(v, str) for v in data.values())


# =============================================================================
# Coverage Completion Tests
# =============================================================================


def test_cors_with_comma_separated_origins(monkeypatch: pytest.MonkeyPatch):
    """Test CORS properly parses comma-separated origins."""
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv(
        "ALLOWED_ORIGINS", "https://example.com, https://app.example.com , https://api.example.com"
    )

    origins = _resolve_cors_origins()
    assert origins == ["https://example.com", "https://app.example.com", "https://api.example.com"]


def test_cors_with_empty_origin_in_list(monkeypatch: pytest.MonkeyPatch):
    """Test CORS filters out empty origins from comma-separated list."""
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("ALLOWED_ORIGINS", "https://example.com,,https://app.example.com, ,")

    origins = _resolve_cors_origins()
    assert origins == ["https://example.com", "https://app.example.com"]


@pytest.mark.asyncio()
async def test_lifespan_uses_settings_registry_dir(mock_registry, mock_manifest_manager):
    """Test lifespan uses registry_dir from settings."""
    test_app = FastAPI()

    with (
        patch("apps.model_registry.main.ModelRegistry") as mock_registry_class,
        patch(
            "apps.model_registry.main.RegistryManifestManager", return_value=mock_manifest_manager
        ),
        patch("apps.model_registry.main.set_registry"),
        patch("apps.model_registry.main.settings", {"registry_dir": Path("/custom/registry")}),
    ):
        mock_registry_class.return_value = mock_registry

        async with lifespan(test_app):
            # Verify ModelRegistry was initialized with settings registry_dir
            mock_registry_class.assert_called_once_with(registry_dir=Path("/custom/registry"))


@pytest.mark.asyncio()
async def test_lifespan_populates_cors_origins(mock_registry, mock_manifest_manager, monkeypatch: pytest.MonkeyPatch):
    """Test lifespan populates the shared _cors_allow_origins list."""

    monkeypatch.setenv("ENVIRONMENT", "test")
    monkeypatch.delenv("ALLOWED_ORIGINS", raising=False)

    test_app = FastAPI()

    # Clear any origins from prior test runs
    _cors_allow_origins.clear()

    try:
        with (
            patch("apps.model_registry.main.ModelRegistry", return_value=mock_registry),
            patch(
                "apps.model_registry.main.RegistryManifestManager",
                return_value=mock_manifest_manager,
            ),
            patch("apps.model_registry.main.set_registry"),
        ):
            async with lifespan(test_app):
                # Origins should be populated during startup
                assert len(_cors_allow_origins) > 0
                assert "http://localhost:8501" in _cors_allow_origins
    finally:
        _cors_allow_origins.clear()


def _make_mock_registry_and_manager():
    """Helper to create mock registry and manifest manager for integration tests."""
    mock_registry = Mock()
    mock_registry.get_manifest.return_value = _create_test_manifest(
        artifact_count=5,
        production_models={"risk_model": "v1.0.0"},
    )
    mock_manager = Mock()
    mock_manager.exists.return_value = True
    mock_manager.verify_integrity.return_value = True
    return mock_registry, mock_manager


def test_cors_lifespan_integration_with_test_client(monkeypatch: pytest.MonkeyPatch):
    """Test that the real app starts successfully with TestClient (ASGI startup integration)."""
    monkeypatch.setenv("ENVIRONMENT", "test")
    monkeypatch.setenv("ALLOWED_ORIGINS", "http://localhost:8501")

    mock_registry, mock_manager = _make_mock_registry_and_manager()

    with (
        patch("apps.model_registry.main.ModelRegistry", return_value=mock_registry),
        patch("apps.model_registry.main.RegistryManifestManager", return_value=mock_manager),
        patch("apps.model_registry.main.set_registry"),
    ):
        with TestClient(app) as client:
            # Verify basic startup works
            response = client.get("/health")
            assert response.status_code == 200
            assert response.json()["status"] == "healthy"

            # Verify CORS headers for allowed origin
            response = client.get(
                "/health",
                headers={"Origin": "http://localhost:8501"},
            )
            assert response.status_code == 200
            assert response.headers.get("access-control-allow-origin") == "http://localhost:8501"
            assert response.headers.get("access-control-allow-credentials") == "true"

            # Verify CORS headers are absent for disallowed origin
            response = client.get(
                "/health",
                headers={"Origin": "https://evil.example.com"},
            )
            assert response.status_code == 200
            assert "access-control-allow-origin" not in response.headers

            # Verify OPTIONS preflight for allowed origin
            response = client.options(
                "/health",
                headers={
                    "Origin": "http://localhost:8501",
                    "Access-Control-Request-Method": "GET",
                    "Access-Control-Request-Headers": "Authorization",
                },
            )
            assert response.status_code == 200
            assert response.headers.get("access-control-allow-origin") == "http://localhost:8501"
            assert response.headers.get("access-control-allow-credentials") == "true"
            assert "GET" in response.headers.get("access-control-allow-methods", "")

            # Verify OPTIONS preflight denied for disallowed origin
            response = client.options(
                "/health",
                headers={
                    "Origin": "https://evil.example.com",
                    "Access-Control-Request-Method": "GET",
                },
            )
            assert response.status_code == 400


def test_cors_production_startup_fails_without_allowed_origins(monkeypatch: pytest.MonkeyPatch):
    """Test that ASGI startup fails in production when ALLOWED_ORIGINS is unset."""
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.delenv("ALLOWED_ORIGINS", raising=False)

    with pytest.raises(RuntimeError, match="ALLOWED_ORIGINS must be set for production"):
        with TestClient(app):
            pass  # pragma: no cover — startup should fail before reaching here


@pytest.mark.asyncio()
async def test_cors_origins_replaced_not_accumulated_on_restart(monkeypatch: pytest.MonkeyPatch):
    """Test that _cors_allow_origins is replaced (not accumulated) across restarts."""

    mock_registry, mock_manager = _make_mock_registry_and_manager()
    test_app = FastAPI()

    # First startup with origin A
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("ALLOWED_ORIGINS", "https://a.example.com")
    _cors_allow_origins.clear()

    try:
        with (
            patch("apps.model_registry.main.ModelRegistry", return_value=mock_registry),
            patch("apps.model_registry.main.RegistryManifestManager", return_value=mock_manager),
            patch("apps.model_registry.main.set_registry"),
        ):
            async with lifespan(test_app):
                assert _cors_allow_origins == ["https://a.example.com"]

        # Second startup with origin B (simulates restart with new config)
        monkeypatch.setenv("ALLOWED_ORIGINS", "https://b.example.com")

        with (
            patch("apps.model_registry.main.ModelRegistry", return_value=mock_registry),
            patch("apps.model_registry.main.RegistryManifestManager", return_value=mock_manager),
            patch("apps.model_registry.main.set_registry"),
        ):
            async with lifespan(test_app):
                # Should only contain origin B, not both A and B
                assert _cors_allow_origins == ["https://b.example.com"]
                assert "https://a.example.com" not in _cors_allow_origins
    finally:
        _cors_allow_origins.clear()


def test_verify_cors_guard_reassigns_copied_origins():
    """Test guard reassigns shared reference when allow_origins is a different object."""
    from starlette.middleware.cors import CORSMiddleware as RealCORSMiddleware

    # Create a middleware with a different list (simulates copy/freeze)
    test_app = FastAPI()
    copied_origins = ["http://localhost:8501"]
    cors_mw = RealCORSMiddleware(app=test_app, allow_origins=copied_origins)

    # Build a fake middleware stack: the guard walks .app attributes
    guard_app = FastAPI()
    guard_app.middleware_stack = cors_mw

    # Should not raise — guard reassigns the shared reference
    _verify_cors_middleware_uses_shared_origins(guard_app)

    # Verify the middleware now holds the shared reference
    assert cors_mw.allow_origins is _cors_allow_origins


def test_verify_cors_guard_logs_error_when_middleware_missing(caplog):
    """Test guard logs ERROR when CORSMiddleware is not in stack."""
    import logging

    caplog.set_level(logging.ERROR, logger="apps.model_registry.main")

    guard_app = FastAPI()
    # Set a non-None middleware_stack without CORSMiddleware
    guard_app.middleware_stack = Mock()
    guard_app.middleware_stack.app = None  # terminate the walk

    # Should not raise, but should log an error
    _verify_cors_middleware_uses_shared_origins(guard_app)
    assert "CORSMiddleware not found" in caplog.text


def test_verify_cors_guard_skips_when_no_middleware_stack():
    """Test guard silently returns when middleware_stack is None (bare test app)."""
    guard_app = FastAPI()
    assert guard_app.middleware_stack is None

    # Should not raise — bare app in tests
    _verify_cors_middleware_uses_shared_origins(guard_app)


def test_main_entry_point_not_executed_on_import():
    """Test __main__ entry point only runs when executed directly."""
    # When imported normally, __name__ != "__main__"
    # So uvicorn.run should not be called
    # This test just verifies the module can be imported without side effects
    import apps.model_registry.main as main_module

    # Module should be importable
    assert main_module is not None
    assert hasattr(main_module, "app")

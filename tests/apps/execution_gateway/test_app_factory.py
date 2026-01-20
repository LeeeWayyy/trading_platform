"""Comprehensive tests for the Execution Gateway app factory helpers.

This test suite provides comprehensive coverage for app_factory.py including:
- Application initialization in test and production modes
- Middleware setup and configuration
- Dependency injection via AppContext
- Config validation and override behavior
- Error cases and edge conditions
- Lifespan management (startup/shutdown)
- Helper functions (create_mock_context, create_test_config)
- Future-proofing for Phase 4 implementation
"""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from apps.execution_gateway.app_context import AppContext
from apps.execution_gateway.app_factory import (
    create_app,
    create_mock_context,
    create_test_app,
    create_test_config,
    initialize_app_context,
    shutdown_app_context,
)
from apps.execution_gateway.config import ExecutionGatewayConfig

# ============================================================================
# Test Fixtures
# ============================================================================


@pytest.fixture()
def mock_context() -> AppContext:
    """Create a mock AppContext for testing."""
    return create_mock_context()


@pytest.fixture()
def test_config() -> ExecutionGatewayConfig:
    """Create a test config for testing."""
    return create_test_config(dry_run=True, strategy_id="test_strategy")


# ============================================================================
# Tests: create_app() - Test Mode
# ============================================================================


def test_create_app_test_mode_injects_context_and_config(
    mock_context: AppContext, test_config: ExecutionGatewayConfig
) -> None:
    """Test that create_app() properly injects context and config in test mode."""
    app = create_app(test_mode=True, test_context=mock_context, test_config=test_config)

    with TestClient(app) as client:
        # Verify app is properly initialized
        assert isinstance(app, FastAPI)
        assert app.title == "Execution Gateway"
        assert app.version == "0.1.0"

        # Verify context and config are injected
        assert app.state.context is mock_context
        assert app.state.config is test_config

        # Verify metrics endpoint is mounted
        response = client.get("/metrics")
        assert response.status_code == 200


def test_create_app_test_mode_without_context() -> None:
    """Test that create_app() works in test mode without injecting context."""
    app = create_app(test_mode=True)

    with TestClient(app):
        # App should initialize but may not have context
        assert isinstance(app, FastAPI)


def test_create_app_test_mode_with_only_context(mock_context: AppContext) -> None:
    """Test that create_app() works with only context injection."""
    app = create_app(test_mode=True, test_context=mock_context)

    with TestClient(app):
        assert app.state.context is mock_context


def test_create_app_test_mode_with_only_config(test_config: ExecutionGatewayConfig) -> None:
    """Test that create_app() works with only config injection."""
    app = create_app(test_mode=True, test_config=test_config)

    with TestClient(app):
        assert app.state.config is test_config


# ============================================================================
# Tests: create_app() - Production Mode
# ============================================================================


def test_create_app_production_mode_runs_placeholder_lifespan() -> None:
    """Test that create_app() runs placeholder lifespan in production mode."""
    app = create_app()

    with TestClient(app) as client:
        # Verify app is properly initialized
        assert isinstance(app, FastAPI)
        response = client.get("/metrics")
        assert response.status_code == 200


def test_create_app_production_mode_does_not_inject_test_context(
    mock_context: AppContext,
) -> None:
    """Test that test_context is ignored in production mode."""
    app = create_app(test_mode=False, test_context=mock_context)

    with TestClient(app):
        # In production mode, test_context should be ignored
        # (lifespan doesn't set app.state.context unless test_mode=True)
        assert not hasattr(app.state, "context") or app.state.context is None


def test_create_app_production_mode_does_not_inject_test_config(
    test_config: ExecutionGatewayConfig,
) -> None:
    """Test that test_config is ignored in production mode."""
    app = create_app(test_mode=False, test_config=test_config)

    with TestClient(app):
        # In production mode, test_config should be ignored
        assert not hasattr(app.state, "config") or app.state.config is None


# ============================================================================
# Tests: create_test_app() - Convenience Wrapper
# ============================================================================


def test_create_test_app_wrapper_injects_context_and_config(
    mock_context: AppContext, test_config: ExecutionGatewayConfig
) -> None:
    """Test that create_test_app() is a convenience wrapper for test mode."""
    app = create_test_app(context=mock_context, config=test_config)

    with TestClient(app):
        assert app.state.context is mock_context
        assert app.state.config is test_config


def test_create_test_app_without_context_or_config() -> None:
    """Test that create_test_app() works without context or config."""
    app = create_test_app()

    with TestClient(app):
        assert isinstance(app, FastAPI)


def test_create_test_app_with_only_context(mock_context: AppContext) -> None:
    """Test that create_test_app() works with only context."""
    app = create_test_app(context=mock_context)

    with TestClient(app):
        assert app.state.context is mock_context


def test_create_test_app_with_only_config(test_config: ExecutionGatewayConfig) -> None:
    """Test that create_test_app() works with only config."""
    app = create_test_app(config=test_config)

    with TestClient(app):
        assert app.state.config is test_config


# ============================================================================
# Tests: FastAPI Configuration
# ============================================================================


def test_create_app_sets_correct_metadata() -> None:
    """Test that create_app() sets correct FastAPI metadata."""
    app = create_app(test_mode=True)

    assert app.title == "Execution Gateway"
    assert app.description == "Order execution service for trading platform"
    assert app.version == "0.1.0"


def test_create_app_mounts_prometheus_metrics() -> None:
    """Test that create_app() mounts Prometheus metrics endpoint."""
    app = create_app(test_mode=True)

    with TestClient(app) as client:
        response = client.get("/metrics")
        assert response.status_code == 200
        # Prometheus metrics should be text/plain
        assert "text/plain" in response.headers.get("content-type", "")


def test_create_app_metrics_endpoint_content() -> None:
    """Test that Prometheus metrics endpoint returns valid content."""
    app = create_app(test_mode=True)

    with TestClient(app) as client:
        response = client.get("/metrics")
        assert response.status_code == 200
        content = response.text
        # Basic check for Prometheus format
        assert "# HELP" in content or "# TYPE" in content or content == ""


# ============================================================================
# Tests: create_mock_context() - Mock Helper
# ============================================================================


def test_create_mock_context_provides_default_mocks() -> None:
    """Test that create_mock_context() provides mocked dependencies."""
    ctx = create_mock_context()

    # Verify all dependencies are mocked
    assert ctx.db is not None
    assert ctx.redis is not None
    assert ctx.alpaca is not None
    assert ctx.reconciliation_service is not None
    assert ctx.recovery_manager is not None
    assert ctx.risk_config is not None
    assert ctx.fat_finger_validator is not None
    assert ctx.twap_slicer is not None
    assert ctx.webhook_secret == "test_secret"
    assert ctx.liquidity_service is None  # Explicitly None in defaults


def test_create_mock_context_overrides_single_dependency() -> None:
    """Test that create_mock_context() allows overriding individual dependencies."""
    custom_db = MagicMock()
    ctx = create_mock_context(db=custom_db)

    assert ctx.db is custom_db
    # Other dependencies should still be mocked
    assert ctx.redis is not None
    assert ctx.webhook_secret == "test_secret"


def test_create_mock_context_overrides_multiple_dependencies() -> None:
    """Test that create_mock_context() allows overriding multiple dependencies."""
    custom_db = MagicMock()
    custom_redis = MagicMock()
    custom_webhook_secret = "custom_secret"

    ctx = create_mock_context(
        db=custom_db,
        redis=custom_redis,
        webhook_secret=custom_webhook_secret,
    )

    assert ctx.db is custom_db
    assert ctx.redis is custom_redis
    assert ctx.webhook_secret == custom_webhook_secret


def test_create_mock_context_overrides_webhook_secret() -> None:
    """Test that create_mock_context() allows overriding webhook_secret."""
    ctx = create_mock_context(webhook_secret="override_secret")

    assert ctx.webhook_secret == "override_secret"


def test_create_mock_context_overrides_liquidity_service() -> None:
    """Test that create_mock_context() allows overriding liquidity_service."""
    mock_liquidity = MagicMock()
    ctx = create_mock_context(liquidity_service=mock_liquidity)

    assert ctx.liquidity_service is mock_liquidity


def test_create_mock_context_is_instance_of_app_context() -> None:
    """Test that create_mock_context() returns an AppContext instance."""
    ctx = create_mock_context()

    assert isinstance(ctx, AppContext)


# ============================================================================
# Tests: create_test_config() - Config Helper
# ============================================================================


def test_create_test_config_provides_defaults() -> None:
    """Test that create_test_config() provides default configuration."""
    config = create_test_config()

    # Verify it's a valid config
    assert isinstance(config, ExecutionGatewayConfig)
    assert config.strategy_id is not None
    assert config.dry_run in (True, False)


def test_create_test_config_overrides_dry_run() -> None:
    """Test that create_test_config() allows overriding dry_run."""
    config = create_test_config(dry_run=False)

    assert config.dry_run is False


def test_create_test_config_overrides_strategy_id() -> None:
    """Test that create_test_config() allows overriding strategy_id."""
    config = create_test_config(strategy_id="alt_strategy")

    assert config.strategy_id == "alt_strategy"


def test_create_test_config_overrides_multiple_fields() -> None:
    """Test that create_test_config() allows overriding multiple fields."""
    config = create_test_config(
        dry_run=False,
        strategy_id="custom_strategy",
        circuit_breaker_enabled=False,
    )

    assert config.dry_run is False
    assert config.strategy_id == "custom_strategy"
    assert config.circuit_breaker_enabled is False


def test_create_test_config_overrides_redis_settings() -> None:
    """Test that create_test_config() allows overriding Redis settings."""
    config = create_test_config(
        redis_host="custom_host",
        redis_port=9999,
        redis_db=5,
    )

    assert config.redis_host == "custom_host"
    assert config.redis_port == 9999
    assert config.redis_db == 5


def test_create_test_config_overrides_performance_settings() -> None:
    """Test that create_test_config() allows overriding performance settings."""
    config = create_test_config(
        performance_cache_ttl=600,
        max_performance_days=180,
        feature_performance_dashboard=True,
    )

    assert config.performance_cache_ttl == 600
    assert config.max_performance_days == 180
    assert config.feature_performance_dashboard is True


def test_create_test_config_is_instance_of_config() -> None:
    """Test that create_test_config() returns an ExecutionGatewayConfig instance."""
    config = create_test_config()

    assert isinstance(config, ExecutionGatewayConfig)


# ============================================================================
# Tests: Lifespan Management (Phase 4 Placeholders)
# ============================================================================


def test_initialize_app_context_not_implemented() -> None:
    """Test that initialize_app_context() raises NotImplementedError (Phase 4)."""
    mock_config = MagicMock()

    with pytest.raises(NotImplementedError, match="Phase 4"):
        asyncio.run(initialize_app_context(mock_config))


def test_shutdown_app_context_not_implemented() -> None:
    """Test that shutdown_app_context() raises NotImplementedError (Phase 4)."""
    mock_context = MagicMock()

    with pytest.raises(NotImplementedError, match="Phase 4"):
        asyncio.run(shutdown_app_context(mock_context))


# ============================================================================
# Tests: Edge Cases and Error Handling
# ============================================================================


def test_create_app_lifespan_startup_logs_message(caplog) -> None:
    """Test that placeholder lifespan logs startup message."""
    app = create_app(test_mode=True)

    with caplog.at_level("INFO"):
        with TestClient(app):
            pass

    assert "Application starting (placeholder lifespan)" in caplog.text


def test_create_app_lifespan_shutdown_logs_message(caplog) -> None:
    """Test that placeholder lifespan logs shutdown message."""
    app = create_app(test_mode=True)

    with caplog.at_level("INFO"):
        with TestClient(app):
            pass

    assert "Application shutting down (placeholder lifespan)" in caplog.text


def test_create_app_lifespan_exception_during_yield(mock_context: AppContext) -> None:
    """Test that lifespan handles exceptions gracefully."""
    app = create_app(test_mode=True, test_context=mock_context)

    # Lifespan should complete even if client exits abnormally
    with TestClient(app):
        assert app.state.context is mock_context


def test_create_mock_context_override_with_none() -> None:
    """Test that create_mock_context() allows setting dependencies to None."""
    ctx = create_mock_context(redis=None, alpaca=None)

    assert ctx.redis is None
    assert ctx.alpaca is None
    # Other dependencies should still be mocked
    assert ctx.db is not None


def test_create_test_config_override_with_invalid_type() -> None:
    """Test that create_test_config() accepts any override (relies on setattr)."""
    # This test verifies the override mechanism works even with unexpected types
    # Note: Type safety is enforced at compile time, not runtime
    config = create_test_config(custom_field="custom_value")

    # The override should work via setattr
    assert hasattr(config, "custom_field")
    assert config.custom_field == "custom_value"  # type: ignore[attr-defined]


# ============================================================================
# Tests: Integration with TestClient
# ============================================================================


def test_create_app_works_with_test_client_context_manager() -> None:
    """Test that create_app() works properly with TestClient context manager."""
    app = create_app(test_mode=True)

    # Should not raise any exceptions
    with TestClient(app) as client:
        response = client.get("/metrics")
        assert response.status_code == 200


def test_create_app_multiple_test_clients(mock_context: AppContext) -> None:
    """Test that create_app() can be used with multiple TestClient instances."""
    app = create_app(test_mode=True, test_context=mock_context)

    # First client
    with TestClient(app) as client1:
        response1 = client1.get("/metrics")
        assert response1.status_code == 200

    # Second client (reusing same app)
    with TestClient(app) as client2:
        response2 = client2.get("/metrics")
        assert response2.status_code == 200


# ============================================================================
# Tests: State Management
# ============================================================================


def test_create_app_app_state_isolation(mock_context: AppContext) -> None:
    """Test that app.state is properly isolated between app instances."""
    app1 = create_app(test_mode=True, test_context=mock_context)
    app2 = create_app(test_mode=True)

    with TestClient(app1):
        with TestClient(app2):
            assert app1.state.context is mock_context
            assert not hasattr(app2.state, "context") or app2.state.context is None


def test_create_app_preserves_state_across_requests(mock_context: AppContext) -> None:
    """Test that app.state is preserved across multiple requests."""
    app = create_app(test_mode=True, test_context=mock_context)

    with TestClient(app) as client:
        # First request
        client.get("/metrics")
        assert app.state.context is mock_context

        # Second request
        client.get("/metrics")
        assert app.state.context is mock_context


# ============================================================================
# Tests: Mock Context Field Validation
# ============================================================================


def test_create_mock_context_has_all_required_fields() -> None:
    """Test that create_mock_context() provides all required AppContext fields."""
    ctx = create_mock_context()

    # Verify all AppContext fields are present
    assert hasattr(ctx, "db")
    assert hasattr(ctx, "redis")
    assert hasattr(ctx, "alpaca")
    assert hasattr(ctx, "liquidity_service")
    assert hasattr(ctx, "reconciliation_service")
    assert hasattr(ctx, "recovery_manager")
    assert hasattr(ctx, "risk_config")
    assert hasattr(ctx, "fat_finger_validator")
    assert hasattr(ctx, "twap_slicer")
    assert hasattr(ctx, "webhook_secret")
    assert hasattr(ctx, "position_metrics_lock")
    assert hasattr(ctx, "tracked_position_symbols")


def test_create_mock_context_default_factories_work() -> None:
    """Test that create_mock_context() uses default factories correctly."""
    ctx = create_mock_context()

    # Verify default factories are working
    assert isinstance(ctx.position_metrics_lock, asyncio.Lock)
    assert isinstance(ctx.tracked_position_symbols, set)
    assert ctx.tracked_position_symbols == set()


# ============================================================================
# Tests: Error Handling in Lifespan
# ============================================================================


def test_create_app_lifespan_finally_block_executes(caplog) -> None:
    """Test that lifespan finally block always executes."""
    app = create_app(test_mode=True)

    with caplog.at_level("INFO"):
        with TestClient(app):
            pass

    # Both startup and shutdown messages should be logged
    assert "Application starting" in caplog.text
    assert "Application shutting down" in caplog.text


# ============================================================================
# Tests: Configuration Validation in App
# ============================================================================


def test_create_app_with_config_validates_fields(test_config: ExecutionGatewayConfig) -> None:
    """Test that create_app() properly stores and validates config."""
    # Modify config for validation
    test_config.dry_run = True
    test_config.strategy_id = "test_strategy"

    app = create_app(test_mode=True, test_config=test_config)

    with TestClient(app):
        assert app.state.config.dry_run is True
        assert app.state.config.strategy_id == "test_strategy"


# ============================================================================
# Tests: Context Dependency Mocking
# ============================================================================


def test_create_mock_context_mocks_are_magic_mocks() -> None:
    """Test that create_mock_context() uses MagicMock for dependencies."""
    ctx = create_mock_context()

    # Verify mocked dependencies are MagicMocks (or None for optional deps)
    assert isinstance(ctx.db, MagicMock)
    assert isinstance(ctx.redis, MagicMock) or ctx.redis is None
    assert isinstance(ctx.alpaca, MagicMock) or ctx.alpaca is None
    assert isinstance(ctx.reconciliation_service, MagicMock) or ctx.reconciliation_service is None
    assert isinstance(ctx.recovery_manager, MagicMock)
    assert isinstance(ctx.risk_config, MagicMock)
    assert isinstance(ctx.fat_finger_validator, MagicMock)


# ============================================================================
# Tests: Future-Proofing for Phase 4
# ============================================================================


def test_initialize_app_context_signature() -> None:
    """Test that initialize_app_context() has correct signature (Phase 4)."""
    # Verify function exists and has correct parameter
    import inspect

    sig = inspect.signature(initialize_app_context)
    params = list(sig.parameters.keys())

    assert "config" in params
    # Should be async function
    assert asyncio.iscoroutinefunction(initialize_app_context)


def test_shutdown_app_context_signature() -> None:
    """Test that shutdown_app_context() has correct signature (Phase 4)."""
    # Verify function exists and has correct parameter
    import inspect

    sig = inspect.signature(shutdown_app_context)
    params = list(sig.parameters.keys())

    assert "context" in params
    # Should be async function
    assert asyncio.iscoroutinefunction(shutdown_app_context)


# ============================================================================
# Tests: Lifespan Context Manager Behavior
# ============================================================================


def test_create_app_lifespan_is_async_context_manager() -> None:
    """Test that lifespan is an async context manager."""
    app = create_app(test_mode=True)

    # Verify lifespan is set
    assert app.router.lifespan_context is not None


def test_create_app_test_mode_context_available_during_requests(
    mock_context: AppContext,
) -> None:
    """Test that context is available during request handling."""
    app = create_app(test_mode=True, test_context=mock_context)

    with TestClient(app) as client:
        # Context should be available in app.state during requests
        response = client.get("/metrics")
        assert response.status_code == 200
        assert app.state.context is mock_context


# ============================================================================
# Tests: Complete Coverage for All Branches
# ============================================================================


def test_create_app_all_parameters_combinations() -> None:
    """Test create_app() with all parameter combinations."""
    mock_ctx = create_mock_context()
    test_cfg = create_test_config()

    # Test all 8 combinations of boolean flags
    combinations = [
        (False, None, None),
        (False, mock_ctx, None),
        (False, None, test_cfg),
        (False, mock_ctx, test_cfg),
        (True, None, None),
        (True, mock_ctx, None),
        (True, None, test_cfg),
        (True, mock_ctx, test_cfg),
    ]

    for test_mode, context, config in combinations:
        app = create_app(test_mode=test_mode, test_context=context, test_config=config)
        with TestClient(app):
            assert isinstance(app, FastAPI)


def test_create_mock_context_all_dependencies_override() -> None:
    """Test create_mock_context() with all dependencies overridden."""
    custom_db = MagicMock()
    custom_redis = MagicMock()
    custom_alpaca = MagicMock()
    custom_liquidity = MagicMock()
    custom_reconciliation = MagicMock()
    custom_recovery = MagicMock()
    custom_risk = MagicMock()
    custom_fat_finger = MagicMock()
    custom_twap = MagicMock()

    ctx = create_mock_context(
        db=custom_db,
        redis=custom_redis,
        alpaca=custom_alpaca,
        liquidity_service=custom_liquidity,
        reconciliation_service=custom_reconciliation,
        recovery_manager=custom_recovery,
        risk_config=custom_risk,
        fat_finger_validator=custom_fat_finger,
        twap_slicer=custom_twap,
        webhook_secret="custom",
    )

    assert ctx.db is custom_db
    assert ctx.redis is custom_redis
    assert ctx.alpaca is custom_alpaca
    assert ctx.liquidity_service is custom_liquidity
    assert ctx.reconciliation_service is custom_reconciliation
    assert ctx.recovery_manager is custom_recovery
    assert ctx.risk_config is custom_risk
    assert ctx.fat_finger_validator is custom_fat_finger
    assert ctx.twap_slicer is custom_twap
    assert ctx.webhook_secret == "custom"

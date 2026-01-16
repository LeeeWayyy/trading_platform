"""Application factory for Execution Gateway.

This module provides factory functions for creating FastAPI applications with
clean dependency injection. It enables:
    - Integration testing (create_app() for test fixtures)
    - Clean composition (all dependencies wired explicitly)
    - Easy mocking (inject test doubles via AppContext)

Design Rationale:
    - Single source of truth for app creation
    - Separates app composition from routing logic
    - Enables testing without touching main.py globals
    - Supports different configurations (test vs production)

Usage:
    # In tests
    from apps.execution_gateway.app_factory import create_app

    async def test_my_route():
        app = create_app(test_mode=True)
        async with TestClient(app) as client:
            response = await client.get("/health")
            assert response.status_code == 200

    # In production (main.py)
    from apps.execution_gateway.app_factory import create_app

    app = create_app()

See REFACTOR_EXECUTION_GATEWAY_TASK.md Phase 0 for design decisions.

PHASE 0 STATUS: This module is a placeholder showing the target pattern.
The actual integration with main.py will happen in Phase 4 when we migrate
the lifespan logic to startup.py/shutdown.py.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any

from fastapi import FastAPI
from prometheus_client import make_asgi_app

if TYPE_CHECKING:
    from apps.execution_gateway.app_context import AppContext
    from apps.execution_gateway.config import ExecutionGatewayConfig

logger = logging.getLogger(__name__)


def create_app(
    *,
    test_mode: bool = False,
    test_context: AppContext | None = None,
    test_config: ExecutionGatewayConfig | None = None,
) -> FastAPI:
    """Create and configure FastAPI application.

    This factory function creates a FastAPI application with all dependencies
    wired up. It supports both production and testing modes.

    Args:
        test_mode: If True, skip external connections (DB, Redis, Alpaca)
        test_context: Optional test AppContext to inject (testing only)
        test_config: Optional test config to inject (testing only)

    Returns:
        FastAPI: Configured application instance

    Usage:
        # Production mode
        app = create_app()

        # Test mode with mocks
        mock_ctx = create_mock_context()
        test_config = create_test_config()
        app = create_app(
            test_mode=True,
            test_context=mock_ctx,
            test_config=test_config,
        )

    Note:
        In Phase 0, this is a placeholder. The actual implementation will
        be completed in Phase 4 when we extract startup.py and shutdown.py.
        For now, main.py continues to use its existing lifespan logic.
    """
    # Phase 4 TODO: Import startup/shutdown logic
    # from apps.execution_gateway.startup import create_lifespan

    @asynccontextmanager
    async def placeholder_lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
        """Placeholder lifespan for Phase 0.

        This will be replaced with proper startup/shutdown logic in Phase 4.
        For now, we just demonstrate the pattern for storing AppContext and
        config in app.state.
        """
        # Phase 4 TODO: Move initialization logic from main.py lifespan here
        logger.info("Application starting (placeholder lifespan)")

        if test_mode and test_context:
            # Test mode: use injected context
            app.state.context = test_context
        else:
            # Production mode: initialize real context
            # Phase 4 TODO: Initialize real AppContext from startup.py
            pass

        if test_mode and test_config:
            # Test mode: use injected config
            app.state.config = test_config
        else:
            # Production mode: load real config
            # Phase 4 TODO: Load config via get_config()
            pass

        try:
            yield
        finally:
            # Phase 4 TODO: Move shutdown logic from main.py lifespan here
            logger.info("Application shutting down (placeholder lifespan)")

    # Create FastAPI app
    app = FastAPI(
        title="Execution Gateway",
        description="Order execution service for trading platform",
        version="0.1.0",  # Phase 4 TODO: Use __version__ from __init__.py
        lifespan=placeholder_lifespan,
    )

    # Mount Prometheus metrics endpoint
    metrics_app = make_asgi_app()
    app.mount("/metrics", metrics_app)

    # Phase 2 TODO: Mount routers from routes/ modules
    # from apps.execution_gateway.routes import health, orders, positions, webhooks
    # app.include_router(health.router, tags=["Health"])
    # app.include_router(orders.router, prefix="/api/v1", tags=["Orders"])
    # app.include_router(positions.router, prefix="/api/v1", tags=["Positions"])
    # app.include_router(webhooks.router, prefix="/api/v1", tags=["Webhooks"])

    # Phase 1 TODO: Add middleware from middleware.py
    # from apps.execution_gateway.middleware import AuthMiddleware
    # app.add_middleware(AuthMiddleware)

    # Phase 4 TODO: Add exception handlers from exception_handlers.py
    # from apps.execution_gateway.exception_handlers import register_exception_handlers
    # register_exception_handlers(app)

    return app


def create_test_app(
    context: AppContext | None = None,
    config: ExecutionGatewayConfig | None = None,
) -> FastAPI:
    """Create FastAPI application for testing.

    This is a convenience wrapper around create_app() that always uses test_mode.

    Args:
        context: Optional test AppContext to inject
        config: Optional test config to inject

    Returns:
        FastAPI: Application configured for testing

    Usage:
        async def test_orders():
            mock_ctx = create_mock_context()
            app = create_test_app(context=mock_ctx)
            async with AsyncClient(app=app, base_url="http://test") as client:
                response = await client.post("/api/v1/orders", json=ORDER)
                assert response.status_code == 200

    Note:
        This is primarily for integration tests. Unit tests should mock
        dependencies directly rather than creating a full app.
    """
    return create_app(test_mode=True, test_context=context, test_config=config)


# ============================================================================
# Lifecycle Helpers (Phase 4)
# ============================================================================
# These functions will be implemented in Phase 4 when we extract startup.py
# and shutdown.py from main.py lifespan logic.


async def initialize_app_context(config: ExecutionGatewayConfig) -> AppContext:
    """Initialize AppContext with all dependencies.

    This function will be implemented in Phase 4 (startup.py) to replace the
    initialization logic currently in main.py lifespan.

    Args:
        config: Application configuration

    Returns:
        AppContext: Initialized application context

    Raises:
        RuntimeError: If initialization fails

    Note:
        Phase 4 TODO: Move initialization from main.py lifespan here:
        - Load secrets
        - Initialize DB client
        - Initialize Redis client (graceful failure)
        - Initialize Alpaca client (if not dry-run)
        - Initialize liquidity service
        - Initialize reconciliation service
        - Initialize recovery manager (kill-switch, circuit-breaker, etc.)
        - Start background tasks (reconciliation loop, slice scheduler)
    """
    raise NotImplementedError("Phase 4: Move initialization from main.py lifespan")


async def shutdown_app_context(context: AppContext) -> None:
    """Shutdown AppContext and clean up resources.

    This function will be implemented in Phase 4 (shutdown.py) to replace the
    cleanup logic currently in main.py lifespan.

    Args:
        context: Application context to shut down

    Note:
        Phase 4 TODO: Move shutdown from main.py lifespan here:
        - Stop background tasks (reconciliation loop, slice scheduler)
        - Close DB connections
        - Close Redis connections
        - Close Alpaca connections
        - Close secret manager
    """
    raise NotImplementedError("Phase 4: Move shutdown from main.py lifespan")


# ============================================================================
# Testing Utilities
# ============================================================================


def create_mock_context(**overrides: Any) -> AppContext:
    """Create a mock AppContext for testing.

    This helper creates a minimal mock AppContext with all dependencies
    stubbed. Individual dependencies can be overridden via kwargs.

    Args:
        **overrides: Attribute overrides (e.g., db=mock_db, redis=mock_redis)

    Returns:
        AppContext: Mock context for testing

    Usage:
        def test_with_mock_db():
            mock_db = MagicMock()
            ctx = create_mock_context(db=mock_db)
            # Use ctx in tests

    Note:
        Phase 0 placeholder - will be fully implemented in Phase 1 when we
        have concrete service implementations to mock.
    """
    from unittest.mock import MagicMock

    from apps.execution_gateway.app_context import AppContext

    # Create mock dependencies
    defaults = {
        "db": MagicMock(),
        "redis": MagicMock(),
        "alpaca": MagicMock(),
        "liquidity_service": None,
        "reconciliation_service": MagicMock(),
        "recovery_manager": MagicMock(),
        "risk_config": MagicMock(),
        "fat_finger_validator": MagicMock(),
        "twap_slicer": MagicMock(),
        "webhook_secret": "test_secret",
    }
    defaults.update(overrides)

    return AppContext(**defaults)


def create_test_config(**overrides: Any) -> ExecutionGatewayConfig:
    """Create a test configuration.

    This helper creates a minimal test configuration with safe defaults.
    Individual values can be overridden via kwargs.

    Args:
        **overrides: Config overrides (e.g., dry_run=True)

    Returns:
        ExecutionGatewayConfig: Test configuration

    Usage:
        def test_dry_run_mode():
            config = create_test_config(dry_run=True)
            assert config.dry_run

    Note:
        Phase 0 placeholder - will be fully implemented once config.py
        is integrated into the application.
    """
    from apps.execution_gateway.config import get_config

    config = get_config()

    # Apply overrides
    for key, value in overrides.items():
        setattr(config, key, value)

    return config

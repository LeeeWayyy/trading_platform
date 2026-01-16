"""FastAPI dependency providers for Execution Gateway.

This module provides dependency injection functions for FastAPI routes,
enabling clean testing and explicit dependencies.

Design Rationale:
    - Centralized dependency providers
    - Easy mocking in tests
    - Type-safe dependency injection
    - Supports both testing and production contexts

Usage:
    from apps.execution_gateway.dependencies import get_context, get_config

    @router.get("/example")
    async def example_route(
        ctx: AppContext = Depends(get_context),
        config: ExecutionGatewayConfig = Depends(get_config),
    ):
        await ctx.db.execute(...)
        if config.dry_run:
            logger.info("Running in DRY_RUN mode")

See REFACTOR_EXECUTION_GATEWAY_TASK.md Phase 0 for design decisions.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated, Any, cast

from fastapi import Depends, Request

if TYPE_CHECKING:
    from apps.execution_gateway.app_context import (
        AlpacaClientProtocol,
        AppContext,
        DatabaseClientProtocol,
        ReconciliationServiceProtocol,
        RecoveryManagerProtocol,
        RedisClientProtocol,
    )
    from apps.execution_gateway.config import ExecutionGatewayConfig


def get_context(request: Request) -> AppContext:
    """Get application context from FastAPI app state.

    This dependency function retrieves the AppContext stored in the FastAPI
    app.state during startup. It enables dependency injection of all
    application dependencies (DB, Redis, Alpaca, etc.) into route handlers.

    Args:
        request: FastAPI request object (provides access to app.state)

    Returns:
        AppContext: Application context with all dependencies

    Raises:
        RuntimeError: If AppContext is not initialized in app.state

    Usage:
        @router.post("/api/v1/orders")
        async def submit_order(
            ctx: AppContext = Depends(get_context),
        ):
            await ctx.db.execute(...)
            ctx.metrics.orders_total.labels(...).inc()

    Note:
        The AppContext is initialized during application startup in the
        lifespan context manager. If this function raises RuntimeError,
        it means the lifespan didn't complete successfully or the
        AppContext wasn't stored in app.state.
    """
    from apps.execution_gateway.app_context import AppContext as AppContextType

    ctx = getattr(request.app.state, "context", None)
    if ctx is None:
        raise RuntimeError(
            "AppContext not initialized in app.state. "
            "This should never happen in production - the lifespan context "
            "manager initializes AppContext before routes are accessible. "
            "If you see this error, check that app_factory.py properly "
            "initializes app.state.context during startup."
        )
    return cast(AppContextType, ctx)


def get_config(request: Request) -> ExecutionGatewayConfig:
    """Get configuration from FastAPI app state.

    This dependency function retrieves the ExecutionGatewayConfig stored in
    the FastAPI app.state during startup. It enables dependency injection of
    configuration into route handlers.

    Args:
        request: FastAPI request object (provides access to app.state)

    Returns:
        ExecutionGatewayConfig: Application configuration

    Raises:
        RuntimeError: If config is not initialized in app.state

    Usage:
        @router.get("/api/v1/config")
        async def get_config_endpoint(
            config: ExecutionGatewayConfig = Depends(get_config),
        ):
            return {"dry_run": config.dry_run}

    Note:
        The configuration is loaded during application startup and stored
        in app.state. If this function raises RuntimeError, it means the
        startup sequence didn't complete successfully.
    """
    from apps.execution_gateway.config import ExecutionGatewayConfig as ConfigType

    config = getattr(request.app.state, "config", None)
    if config is None:
        raise RuntimeError(
            "ExecutionGatewayConfig not initialized in app.state. "
            "This should never happen in production - the lifespan context "
            "manager initializes config before routes are accessible. "
            "If you see this error, check that app_factory.py properly "
            "initializes app.state.config during startup."
        )
    return cast(ConfigType, config)


def get_version(request: Request) -> str:
    """Get service version from FastAPI app state.

    This dependency function retrieves the service version stored in
    the FastAPI app.state during startup.

    Args:
        request: FastAPI request object (provides access to app.state)

    Returns:
        str: Service version string

    Raises:
        RuntimeError: If version is not initialized in app.state

    Usage:
        @router.get("/version")
        async def get_version_endpoint(
            version: str = Depends(get_version),
        ):
            return {"version": version}

    Note:
        The version is stored during application startup.
    """
    version = getattr(request.app.state, "version", None)
    if version is None:
        raise RuntimeError(
            "Version not initialized in app.state. "
            "This should never happen in production - the lifespan context "
            "manager initializes version before routes are accessible."
        )
    return cast(str, version)


# ============================================================================
# Granular Component Dependencies (Phase 2B)
# ============================================================================
# Individual dependency providers for specific components.
# These enable more granular dependency injection while maintaining
# the benefits of AppContext for testing and modularity.


def get_db_client(ctx: AppContext = Depends(get_context)) -> DatabaseClientProtocol:
    """Get database client from context.

    Args:
        ctx: Application context (injected)

    Returns:
        DatabaseClient: Database client for persistent storage
    """
    from apps.execution_gateway.app_context import DatabaseClientProtocol

    return ctx.db


def get_redis_client(ctx: AppContext = Depends(get_context)) -> RedisClientProtocol | None:
    """Get Redis client from context.

    Args:
        ctx: Application context (injected)

    Returns:
        RedisClient or None: Redis client for caching (None if unavailable)
    """
    from apps.execution_gateway.app_context import RedisClientProtocol

    return ctx.redis


def get_alpaca_client(ctx: AppContext = Depends(get_context)) -> AlpacaClientProtocol | None:
    """Get Alpaca client from context.

    Args:
        ctx: Application context (injected)

    Returns:
        AlpacaExecutor or None: Alpaca broker client (None in dry-run)
    """
    from apps.execution_gateway.app_context import AlpacaClientProtocol

    return ctx.alpaca


def get_recovery_manager(ctx: AppContext = Depends(get_context)) -> RecoveryManagerProtocol:
    """Get recovery manager from context.

    Args:
        ctx: Application context (injected)

    Returns:
        RecoveryManager: Coordinator for safety components
    """
    from apps.execution_gateway.app_context import RecoveryManagerProtocol

    return ctx.recovery_manager


def get_reconciliation_service(
    ctx: AppContext = Depends(get_context),
) -> ReconciliationServiceProtocol | None:
    """Get reconciliation service from context.

    Args:
        ctx: Application context (injected)

    Returns:
        ReconciliationService or None: Broker state sync service (None if unavailable)
    """
    from apps.execution_gateway.app_context import ReconciliationServiceProtocol

    return ctx.reconciliation_service


def get_fat_finger_validator(ctx: AppContext = Depends(get_context)) -> object:
    """Get fat-finger validator from context.

    Args:
        ctx: Application context (injected)

    Returns:
        FatFingerValidator: Order size validation
    """
    return ctx.fat_finger_validator


def get_liquidity_service(ctx: AppContext = Depends(get_context)) -> object | None:
    """Get liquidity service from context.

    Args:
        ctx: Application context (injected)

    Returns:
        LiquidityService or None: ADV lookup service (None if unavailable)
    """
    return ctx.liquidity_service


def get_twap_slicer(ctx: AppContext = Depends(get_context)) -> object:
    """Get TWAP slicer from context.

    Args:
        ctx: Application context (injected)

    Returns:
        TWAPSlicer: TWAP order slicing logic
    """
    return ctx.twap_slicer


def get_metrics(request: Request) -> dict[str, object]:
    """Get Prometheus metrics from FastAPI app state.

    This dependency function retrieves Prometheus metrics stored in
    the FastAPI app.state during startup. The metrics are stored as
    a dictionary mapping metric names to Prometheus objects (Gauge, Counter, Histogram).

    Args:
        request: FastAPI request object (provides access to app.state)

    Returns:
        dict: Prometheus metrics registry

    Raises:
        RuntimeError: If metrics are not initialized in app.state

    Usage:
        @router.get("/health")
        async def health_check(
            metrics: dict = Depends(get_metrics),
        ):
            metrics["database_connection_status"].set(1)

    Note:
        The metrics are defined and stored during application startup.
    """
    metrics = getattr(request.app.state, "metrics", None)
    if metrics is None:
        raise RuntimeError(
            "Metrics not initialized in app.state. "
            "This should never happen in production - the lifespan context "
            "manager initializes metrics before routes are accessible."
        )
    return cast(dict[str, object], metrics)


# ============================================================================
# Testing Support
# ============================================================================


class TestContextOverride:
    """Context manager for overriding AppContext in tests.

    This helper enables clean dependency injection in tests by temporarily
    replacing the AppContext in app.state with a test double.

    Usage:
        async def test_my_route():
            mock_ctx = create_mock_context()
            async with TestContextOverride(app, mock_ctx):
                response = await client.get("/api/v1/example")
                assert response.status_code == 200

    Note:
        This is a testing utility only. In production, the AppContext is
        initialized once during startup and never changed.
    """

    def __init__(self, app: Any, test_context: AppContext) -> None:
        """Initialize context override.

        Args:
            app: FastAPI application instance
            test_context: Mock or test AppContext to inject
        """
        self.app = app
        self.test_context = test_context
        self.original_context: AppContext | None = None

    def __enter__(self) -> AppContext:
        """Enter context manager and store original context."""
        self.original_context = getattr(self.app.state, "context", None)
        self.app.state.context = self.test_context
        return self.test_context

    def __exit__(self, exc_type: object, exc_val: object, exc_tb: object) -> None:
        """Exit context manager and restore original context."""
        if self.original_context is not None:
            self.app.state.context = self.original_context
        else:
            delattr(self.app.state, "context")

    async def __aenter__(self) -> AppContext:
        """Enter async context manager and store original context."""
        return self.__enter__()

    async def __aexit__(self, exc_type: object, exc_val: object, exc_tb: object) -> None:
        """Exit async context manager and restore original context."""
        self.__exit__(exc_type, exc_val, exc_tb)


class TestConfigOverride:
    """Context manager for overriding config in tests.

    This helper enables clean configuration injection in tests by temporarily
    replacing the config in app.state with a test configuration.

    Usage:
        async def test_dry_run_mode():
            test_config = create_test_config(dry_run=True)
            async with TestConfigOverride(app, test_config):
                response = await client.post("/api/v1/orders", json=ORDER)
                # Verify dry-run behavior

    Note:
        This is a testing utility only. In production, the config is
        loaded once during startup and never changed.
    """

    def __init__(self, app: Any, test_config: ExecutionGatewayConfig) -> None:
        """Initialize config override.

        Args:
            app: FastAPI application instance
            test_config: Test configuration to inject
        """
        self.app = app
        self.test_config = test_config
        self.original_config: ExecutionGatewayConfig | None = None

    def __enter__(self) -> ExecutionGatewayConfig:
        """Enter context manager and store original config."""
        self.original_config = getattr(self.app.state, "config", None)
        self.app.state.config = self.test_config
        return self.test_config

    def __exit__(self, exc_type: object, exc_val: object, exc_tb: object) -> None:
        """Exit context manager and restore original config."""
        if self.original_config is not None:
            self.app.state.config = self.original_config
        else:
            delattr(self.app.state, "config")

    async def __aenter__(self) -> ExecutionGatewayConfig:
        """Enter async context manager and store original config."""
        return self.__enter__()

    async def __aexit__(self, exc_type: object, exc_val: object, exc_tb: object) -> None:
        """Exit async context manager and restore original config."""
        self.__exit__(exc_type, exc_val, exc_tb)

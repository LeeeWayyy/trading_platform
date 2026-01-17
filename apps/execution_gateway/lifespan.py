"""Lifespan startup/shutdown orchestration for the Execution Gateway."""

from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass
from typing import Any

import psycopg
from fastapi import FastAPI
from redis.exceptions import RedisError

from apps.execution_gateway.alpaca_client import (
    AlpacaConnectionError,
    AlpacaExecutor,
    AlpacaValidationError,
)
from apps.execution_gateway.app_context import AppContext
from apps.execution_gateway.config import get_config as load_config
from apps.execution_gateway.database import DatabaseClient
from apps.execution_gateway.fat_finger_validator import FatFingerValidator
from apps.execution_gateway.liquidity_service import LiquidityService
from apps.execution_gateway.order_slicer import TWAPSlicer
from apps.execution_gateway.reconciliation import ReconciliationService
from apps.execution_gateway.recovery_manager import RecoveryManager
from apps.execution_gateway.slice_scheduler import SliceScheduler
from config.settings import get_settings
from libs.core.common.secrets import (
    close_secret_manager,
    get_optional_secret,
    get_optional_secret_or_none,
    get_required_secret,
    validate_required_secrets,
)
from libs.core.redis_client import RedisClient, RedisConnectionError
from libs.trading.risk_management import CircuitBreaker, KillSwitch, PositionReservation, RiskConfig

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class LifespanSettings:
    dry_run: bool
    strategy_id: str
    environment: str
    alpaca_base_url: str
    alpaca_paper: bool
    alpaca_data_feed: str | None
    liquidity_check_enabled: bool
    redis_host: str
    redis_port: int
    redis_db: int
    version: str
    risk_config: RiskConfig
    fat_finger_validator: FatFingerValidator
    twap_slicer: TWAPSlicer


@dataclass(slots=True)
class LifespanResources:
    db_client: DatabaseClient
    redis_client: RedisClient | None
    alpaca_client: AlpacaExecutor | None
    webhook_secret: str
    liquidity_service: LiquidityService | None
    recovery_manager: RecoveryManager
    reconciliation_service: ReconciliationService | None
    reconciliation_task: asyncio.Task[None] | None
    zombie_recovery_task: asyncio.Task[None] | None = None


def _is_reconciliation_ready(
    settings: LifespanSettings, resources: LifespanResources
) -> bool:
    """Return True when startup reconciliation gate is open."""
    if settings.dry_run:
        return True
    if resources.reconciliation_service is None:
        return False
    return resources.reconciliation_service.is_startup_complete()


async def _recover_zombie_slices_after_reconciliation(
    settings: LifespanSettings, resources: LifespanResources
) -> None:
    """Recover pending TWAP slices after reconciliation gate opens."""
    recovery_manager = resources.recovery_manager
    if not recovery_manager:
        logger.warning("Recovery manager unavailable; skipping zombie slice recovery")
        return
    slice_scheduler = recovery_manager.slice_scheduler
    if not slice_scheduler:
        logger.warning("Slice scheduler unavailable; skipping zombie slice recovery")
        return
    if not settings.dry_run and resources.reconciliation_service is None:
        logger.error("Reconciliation service unavailable; skipping zombie slice recovery")
        return

    poll_interval_seconds = 1.0
    while not _is_reconciliation_ready(settings, resources):
        if resources.reconciliation_service and resources.reconciliation_service.startup_timed_out():
            logger.error("Startup reconciliation timed out; skipping zombie slice recovery")
            return
        await asyncio.sleep(poll_interval_seconds)

    await asyncio.to_thread(slice_scheduler.recover_zombie_slices)


async def startup_execution_gateway(
    app: FastAPI,
    settings: LifespanSettings,
    metrics: dict[str, Any],
) -> LifespanResources:
    """Run startup initialization and return constructed resources."""
    from apps.execution_gateway.api.dependencies import get_db_pool

    logger.info("Execution Gateway started")
    logger.info("DRY_RUN mode: %s", settings.dry_run)
    logger.info("Strategy ID: %s", settings.strategy_id)
    resources: LifespanResources | None = None
    db_client: DatabaseClient | None = None
    redis_client: RedisClient | None = None
    alpaca_client: AlpacaExecutor | None = None
    liquidity_service: LiquidityService | None = None
    webhook_secret = ""
    recovery_manager: RecoveryManager | None = None
    try:
        # 1. Validate required secrets BEFORE any external connections
        required = ["database/url"]
        if not settings.dry_run:
            required.extend(["alpaca/api_key_id", "alpaca/api_secret_key"])
        if settings.environment not in ("dev", "test"):
            required.append("webhook/secret")
        if os.getenv("REDIS_AUTH_REQUIRED", "false").lower() == "true":
            required.append("redis/password")

        validate_required_secrets(required)

        # 2. Load secrets and initialize clients INSIDE lifespan
        database_url = get_required_secret("database/url")
        db_client = DatabaseClient(database_url)

        # Redis client (for real-time price lookups from Market Data Service)
        redis_password = get_optional_secret_or_none("redis/password")
        # SECURITY: If Redis auth is required, fail-fast when password is missing
        # This ensures safety components depending on Redis work correctly
        if os.getenv("REDIS_AUTH_REQUIRED", "false").lower() == "true" and not redis_password:
            raise RuntimeError(
                "REDIS_AUTH_REQUIRED=true but redis/password is missing or empty. "
                "Set redis/password in your secrets backend. "
                "This is required for authenticated Redis in production."
            )
        try:
            redis_client = RedisClient(
                host=settings.redis_host,
                port=settings.redis_port,
                db=settings.redis_db,
                password=redis_password,
            )
            logger.info("Redis client initialized successfully")
        except (RedisError, RedisConnectionError) as exc:
            # Service should start even if Redis is misconfigured or unavailable
            logger.warning(
                "Failed to initialize Redis client: %s. Real-time P&L will fall back to database prices.",
                exc,
            )
            redis_client = None

        # Webhook secret: REQUIRED in production, optional in dev/test
        # SECURITY: In production, fail startup if webhook secret is missing
        # This prevents a running service that rejects all webhooks (missed fills)
        if settings.environment not in ("dev", "test"):
            webhook_secret = get_required_secret("webhook/secret")
            if not webhook_secret:
                raise RuntimeError(
                    "WEBHOOK_SECRET is required in production but not configured. "
                    "Set the webhook/secret in your secrets backend. "
                    "This prevents a running service that cannot receive Alpaca webhooks."
                )
        else:
            webhook_secret = get_optional_secret("webhook/secret", "")

        # Alpaca client and liquidity service (only if not in dry run mode)
        # Note: get_required_secret raises on missing/empty, so no additional check needed
        if not settings.dry_run:
            alpaca_api_key_id = get_required_secret("alpaca/api_key_id")
            alpaca_api_secret_key = get_required_secret("alpaca/api_secret_key")

            try:
                alpaca_client = AlpacaExecutor(
                    api_key=alpaca_api_key_id,
                    secret_key=alpaca_api_secret_key,
                    base_url=settings.alpaca_base_url,
                    paper=settings.alpaca_paper,
                )
                logger.info("Alpaca client initialized successfully")

                # Liquidity service (ADV lookup for TWAP slicing) - reuses same credentials
                if settings.liquidity_check_enabled:
                    liquidity_service = LiquidityService(
                        api_key=alpaca_api_key_id,
                        api_secret=alpaca_api_secret_key,
                        data_feed=settings.alpaca_data_feed,
                    )
                    logger.info("Liquidity service initialized successfully")
            except AlpacaConnectionError as exc:
                logger.error(
                    "Failed to initialize Alpaca client",
                    extra={"error": str(exc), "error_type": type(exc).__name__},
                    exc_info=True,
                )
            except AlpacaValidationError as exc:
                logger.error(
                    "Invalid Alpaca credentials",
                    extra={"error": str(exc), "error_type": type(exc).__name__},
                    exc_info=True,
                )
            except (TypeError, ValueError, KeyError) as exc:
                logger.error(
                    "Configuration error initializing Alpaca services",
                    extra={"error": str(exc), "error_type": type(exc).__name__},
                    exc_info=True,
                )
            except OSError as exc:
                logger.error(
                    "Network or I/O error initializing Alpaca services",
                    extra={"error": str(exc), "error_type": type(exc).__name__},
                    exc_info=True,
                )

        # Recovery manager orchestrates safety components and slice scheduler (fail-closed)
        recovery_manager = RecoveryManager(
            redis_client=redis_client,
            db_client=db_client,
            executor=alpaca_client,
        )

        resources = LifespanResources(
            db_client=db_client,
            redis_client=redis_client,
            alpaca_client=alpaca_client,
            webhook_secret=webhook_secret,
            liquidity_service=liquidity_service,
            recovery_manager=recovery_manager,
            reconciliation_service=None,
            reconciliation_task=None,
        )

        # Initialize safety components (fail-closed on any error)
        recovery_manager.initialize_kill_switch(
            lambda: KillSwitch(redis_client=redis_client)  # type: ignore[arg-type]
        )
        recovery_manager.initialize_circuit_breaker(
            lambda: CircuitBreaker(redis_client=redis_client)  # type: ignore[arg-type]
        )
        recovery_manager.initialize_position_reservation(
            lambda: PositionReservation(redis=redis_client)  # type: ignore[arg-type]
        )

        # Slice Scheduler (for time-based TWAP slice execution)
        if recovery_manager.kill_switch and recovery_manager.circuit_breaker:
            try:
                recovery_manager.slice_scheduler = SliceScheduler(
                    kill_switch=recovery_manager.kill_switch,
                    breaker=recovery_manager.circuit_breaker,
                    db_client=db_client,
                    executor=alpaca_client,
                )
                logger.info("Slice scheduler initialized (not started yet)")
            except TypeError as exc:
                logger.error(
                    "Failed to initialize slice scheduler - invalid parameters",
                    extra={"error": str(exc), "error_type": type(exc).__name__},
                    exc_info=True,
                )
            except ValueError as exc:
                logger.error(
                    "Failed to initialize slice scheduler - invalid configuration",
                    extra={"error": str(exc), "error_type": type(exc).__name__},
                    exc_info=True,
                )
            except (AttributeError, ImportError) as exc:
                logger.error(
                    "Failed to initialize slice scheduler - module or attribute error",
                    extra={"error": str(exc), "error_type": type(exc).__name__},
                    exc_info=True,
                )
        else:
            logger.warning(
                "Slice scheduler not initialized (kill-switch or circuit-breaker unavailable)"
            )

        # Internal token check
        runtime_settings = get_settings()
        if runtime_settings.internal_token_required:
            secret_value = runtime_settings.internal_token_secret.get_secret_value()
            if not secret_value:
                logger.warning(
                    "INTERNAL_TOKEN_REQUIRED=true but INTERNAL_TOKEN_SECRET is not configured",
                    extra={"warning": "API authentication will fail"},
                )

        # Open async database pool for auth/session validation
        async_db_pool = get_db_pool()
        await async_db_pool.open()
        logger.info("Async database pool opened")

        # Check database connection
        if not db_client.check_connection():
            logger.error("Database connection failed at startup!")
        else:
            logger.info("Database connection OK")

        # Check Alpaca connection (if not DRY_RUN)
        if not settings.dry_run and alpaca_client:
            if not alpaca_client.check_connection():
                logger.warning("Alpaca connection failed at startup!")
            else:
                logger.info("Alpaca connection OK")

        # Start slice scheduler (for TWAP order execution)
        slice_scheduler = recovery_manager.slice_scheduler if recovery_manager else None
        if slice_scheduler:
            if not slice_scheduler.scheduler.running:
                slice_scheduler.start()
                logger.info("Slice scheduler started")
            else:
                logger.info("Slice scheduler already running (skipping start)")
        else:
            logger.warning("Slice scheduler not available (not started)")

        # Start reconciliation service (startup gating + periodic sync)
        reconciliation_service: ReconciliationService | None = None
        reconciliation_task: asyncio.Task[None] | None = None
        if settings.dry_run:
            logger.info("DRY_RUN enabled - skipping reconciliation startup gating")
        elif not alpaca_client:
            logger.error(
                "Alpaca client unavailable - reconciliation not started (gating remains active)"
            )
        else:
            reconciliation_service = ReconciliationService(
                db_client=db_client,
                alpaca_client=alpaca_client,
                redis_client=redis_client,
                dry_run=settings.dry_run,
            )
            await reconciliation_service.run_startup_reconciliation()
            reconciliation_task = asyncio.create_task(reconciliation_service.run_periodic_loop())
            logger.info("Reconciliation service started")

        resources.reconciliation_service = reconciliation_service
        resources.reconciliation_task = reconciliation_task

        # Recover any pending TWAP slices after reconciliation gate opens
        resources.zombie_recovery_task = asyncio.create_task(
            _recover_zombie_slices_after_reconciliation(settings, resources)
        )

        # ========== INITIALIZE APP STATE (Phase 2B) ==========
        # Store all dependencies in app.state for Depends() pattern
        # This enables FastAPI's native dependency injection instead of factory pattern

        logger.info("Initializing app.state for dependency injection...")

        # Store version
        app.state.version = settings.version

        # Store config (create from current environment variables)
        app.state.config = load_config()

        # Store context (create AppContext with all dependencies)
        app.state.context = AppContext(
            db=db_client,
            redis=redis_client,
            alpaca=alpaca_client,
            liquidity_service=liquidity_service,
            reconciliation_service=reconciliation_service,
            recovery_manager=recovery_manager,
            risk_config=settings.risk_config,
            fat_finger_validator=settings.fat_finger_validator,
            twap_slicer=settings.twap_slicer,
            webhook_secret=webhook_secret,
        )

        # Store metrics (create dict for easy access via Depends())
        app.state.metrics = metrics

        logger.info("App.state initialized successfully")

        # ========== MOUNT ROUTERS (Phase 2) ==========
        # Mount routers after all dependencies are initialized
        # Phase 2B: Routers using Depends() are mounted at module level (see below app creation)
        # Phase 2A: Routers using factory pattern are still mounted here

        logger.info("Mounting API routers (factory pattern - Phase 2A)...")

        # All routers now mounted at module level (Phase 2B refactoring complete)
        # See module-level router mounting section below app creation
        # - Health router
        # - Admin router
        # - Reconciliation router
        # - Orders router
        # - Positions router
        # - Webhooks router
        # - Slicing router
        # NOTE: Reconciliation router now mounted at module level (Phase 2B refactoring)
        # See module-level router mounting section below

        logger.info("All API routers mounted successfully")

        return resources
    except Exception:
        if resources is not None:
            await shutdown_execution_gateway(resources)
        else:
            if db_client:
                db_client.close()
            close_secret_manager()
        raise


async def shutdown_execution_gateway(resources: LifespanResources) -> None:
    """Run shutdown cleanup for execution gateway resources."""
    from apps.execution_gateway.api.dependencies import get_db_pool

    logger.info("Execution Gateway shutting down")

    # Shutdown slice scheduler (wait for running jobs to complete)
    slice_scheduler = resources.recovery_manager.slice_scheduler if resources.recovery_manager else None
    if slice_scheduler:
        logger.info("Shutting down slice scheduler...")
        slice_scheduler.shutdown(wait=True)
        logger.info("Slice scheduler shutdown complete")

    # Close async database pool for auth/session validation
    # Guard: only close if pool was initialized (prevents masking startup failures)
    if get_db_pool.cache_info().currsize > 0:
        try:
            async_db_pool = get_db_pool()
            await async_db_pool.close()
            logger.info("Async database pool closed")
        except psycopg.OperationalError as exc:
            logger.warning(
                "Error closing async database pool - connection error",
                extra={"error": str(exc), "error_type": type(exc).__name__},
                exc_info=True,
            )
        except (RuntimeError, AttributeError) as exc:
            logger.warning(
                "Error closing async database pool - pool state error",
                extra={"error": str(exc), "error_type": type(exc).__name__},
                exc_info=True,
            )

    # Close database connection pool for clean shutdown
    if resources.db_client:
        resources.db_client.close()
        logger.info("Database connection pool closed")

    # Stop reconciliation task
    if resources.reconciliation_service:
        resources.reconciliation_service.stop()

    # Cancel and await background tasks for clean shutdown
    tasks_to_cancel = []
    if resources.reconciliation_task and not resources.reconciliation_task.done():
        resources.reconciliation_task.cancel()
        tasks_to_cancel.append(resources.reconciliation_task)
    if resources.zombie_recovery_task and not resources.zombie_recovery_task.done():
        resources.zombie_recovery_task.cancel()
        tasks_to_cancel.append(resources.zombie_recovery_task)

    if tasks_to_cancel:
        await asyncio.gather(*tasks_to_cancel, return_exceptions=True)
        logger.info(f"Cancelled {len(tasks_to_cancel)} background task(s)")

    # Close secret manager
    close_secret_manager()
    logger.info("Secret manager closed")

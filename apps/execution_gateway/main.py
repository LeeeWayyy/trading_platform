"""
Execution Gateway FastAPI Application - T4 Implementation.

This is the main FastAPI application for order execution with:
- POST /api/v1/orders - Submit orders with idempotency
- POST /api/v1/webhooks/orders - Receive order updates from Alpaca
- GET /api/v1/orders/{client_order_id} - Query order status
- GET /api/v1/positions - Get current positions
- GET /health - Health check

Key Features:
- Idempotent order submission via deterministic client_order_id
- DRY_RUN mode for safe testing (controlled by environment variable)
- Real-time order status updates via webhooks
- Position tracking from order fills
- Automatic retry with exponential backoff

Environment Variables:
    ALPACA_API_KEY_ID: Alpaca API key
    ALPACA_API_SECRET_KEY: Alpaca secret key
    ALPACA_BASE_URL: Alpaca API URL (paper or live)
    DATABASE_URL: PostgreSQL connection string
    STRATEGY_ID: Strategy identifier (e.g., "alpha_baseline")
    DRY_RUN: Enable dry run mode (true/false, default: true)
    LOG_LEVEL: Logging level (default: INFO)

Usage:
    # Development (DRY_RUN=true)
    $ uvicorn apps.execution_gateway.main:app --reload --port 8002

    # Production (DRY_RUN=false)
    $ uvicorn apps.execution_gateway.main:app --host 0.0.0.0 --port 8002

See ADR-0014 for architecture decisions.
"""

import asyncio
import hashlib
import hmac
import json
import logging
import os
import time
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any, Literal, Protocol, cast

import psycopg
import redis.exceptions
from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response, status
from fastapi.responses import JSONResponse
from prometheus_client import Counter, Gauge, Histogram, make_asgi_app
from psycopg.errors import UniqueViolation
from pydantic import ValidationError
from redis.exceptions import RedisError
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

from apps.execution_gateway import __version__
from apps.execution_gateway.alpaca_client import (
    AlpacaConnectionError,
    AlpacaExecutor,
    AlpacaRejectionError,
    AlpacaValidationError,
)
from apps.execution_gateway.api.manual_controls import router as manual_controls_router
from apps.execution_gateway.database import (  # noqa: F401 (re-export for tests)
    TERMINAL_STATUSES,
    DatabaseClient,
    calculate_position_update,
    status_rank_for,
)
from apps.execution_gateway.fat_finger_validator import (
    FatFingerValidator,
    iter_breach_types,
)
from apps.execution_gateway.liquidity_service import LiquidityService
from apps.execution_gateway.order_id_generator import (
    generate_client_order_id,
    reconstruct_order_params_hash,
)
from apps.execution_gateway.order_slicer import TWAPSlicer
from apps.execution_gateway.reconciliation import (
    SOURCE_PRIORITY_MANUAL,
    SOURCE_PRIORITY_WEBHOOK,
    ReconciliationService,
)
from apps.execution_gateway.recovery_manager import RecoveryManager
from apps.execution_gateway.schemas import (
    AccountInfoResponse,
    CircuitBreakerStatusResponse,
    ConfigResponse,
    DailyPerformanceResponse,
    DailyPnL,
    ErrorResponse,
    FatFingerThresholds,
    FatFingerThresholdsResponse,
    FatFingerThresholdsUpdateRequest,
    HealthResponse,
    KillSwitchDisengageRequest,
    KillSwitchEngageRequest,
    MarketPricePoint,
    OrderDetail,
    OrderRequest,
    OrderResponse,
    PerformanceRequest,
    Position,
    PositionsResponse,
    RealtimePnLResponse,
    RealtimePositionPnL,
    ReconciliationFillsBackfillRequest,
    ReconciliationForceCompleteRequest,
    SliceDetail,
    SlicingPlan,
    SlicingRequest,
    StrategiesListResponse,
    StrategyStatusResponse,
)
from apps.execution_gateway.slice_scheduler import SliceScheduler
from apps.execution_gateway.webhook_security import (
    extract_signature_from_header,
    verify_webhook_signature,
)

# PHASE 0 REFACTOR: New modules for clean architecture
# These modules are created in Phase 0 but will be fully integrated in later phases.
# Phase 0: Foundation modules (config, metrics, app_context, dependencies, app_factory)
# Phase 1: Extract services and middleware
# Phase 2: Extract routes
# Phase 3: Refactor reconciliation
# Phase 4: Complete migration and cleanup
# See REFACTOR_EXECUTION_GATEWAY_TASK.md for details.
from apps.execution_gateway.app_context import AppContext  # noqa: F401 (Phase 4)
from apps.execution_gateway.app_factory import create_app  # noqa: F401 (Phase 4)
from apps.execution_gateway.config import (  # noqa: F401 (Phase 4)
    ExecutionGatewayConfig,
    get_config,
)
from apps.execution_gateway.dependencies import (  # noqa: F401 (Phase 2+)
    get_config as get_config_dependency,
    get_context,
)

# Phase 1 TODO: Replace inline metrics with imports from metrics.py
# This will prevent duplicate Prometheus registrations. For now, keeping them separate.
# from apps.execution_gateway.metrics import (
#     alpaca_api_requests_total,
#     alpaca_connection_status,
#     ...
# )


# Phase 1: Service modules for clean architecture
from apps.execution_gateway.middleware import (
    populate_user_from_headers as populate_user_from_headers_middleware,
)
from apps.execution_gateway.services.auth_helpers import build_user_context
from apps.execution_gateway.services.order_helpers import (
    batch_fetch_realtime_prices_from_redis,
    create_fat_finger_thresholds_snapshot,
    handle_idempotency_race,
    parse_webhook_timestamp,
    resolve_fat_finger_context,
)
from apps.execution_gateway.services.performance_cache import (
    create_performance_cache_index_key,
    create_performance_cache_key,
    invalidate_performance_cache,
    register_performance_cache,
)
from apps.execution_gateway.services.pnl_calculator import (
    calculate_position_pnl,
    compute_daily_performance,
    resolve_and_calculate_pnl,
)

from config.settings import get_settings
from libs.core.common.api_auth_dependency import (
    APIAuthConfig,
    AuthContext,
    api_auth,
)
from libs.core.common.rate_limit_dependency import RateLimitConfig, rate_limit
from libs.core.common.secrets import (
    close_secret_manager,
    get_optional_secret,
    get_optional_secret_or_none,
    get_required_secret,
    validate_required_secrets,
)
from libs.core.redis_client import RedisClient, RedisConnectionError, RedisKeys

# DESIGN DECISION: Shared auth library in libs/ instead of importing from apps.web_console.
# This prevents backend→frontend dependency while sharing RBAC logic across services.
# Alternative: Import from apps.web_console with runtime guards, rejected due to circular
# dependency risk and tight coupling between frontend/backend deployment cycles.
from libs.platform.web_console_auth.permissions import (
    Permission,
    get_authorized_strategies,
    has_permission,
    require_permission,
)
from libs.trading.risk_management import (
    CircuitBreaker,
    KillSwitch,
    PositionReservation,
    RiskConfig,
)
# Phase 2: Router modules for clean architecture
from apps.execution_gateway.routes.admin import create_admin_router
# Health router imported at module level (line ~862) - Phase 2B refactored to Depends() pattern
from apps.execution_gateway.routes.orders import create_orders_router
from apps.execution_gateway.routes.positions import create_positions_router
# Reconciliation router imported at module level (line ~870) - Phase 2B refactored to Depends() pattern
from apps.execution_gateway.routes.slicing import create_slicing_router
from apps.execution_gateway.routes.webhooks import create_webhooks_router


# ============================================================================
# Configuration
# ============================================================================

# Logging
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
logging.basicConfig(level=LOG_LEVEL, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)


# Env parsing helpers
def _get_float_env(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return float(raw)
    except ValueError:
        logger.warning("Invalid float for %s=%s; using default=%s", name, raw, default)
        return default


def _get_decimal_env(name: str, default: Decimal) -> Decimal:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return Decimal(raw)
    except (ValueError, InvalidOperation):
        logger.warning("Invalid decimal for %s=%s; using default=%s", name, raw, default)
        return default


# Legacy TWAP slicer interval (seconds). Legacy plans scheduled slices once per minute
# and did not persist the interval, so backward-compatibility fallbacks must only apply
# when callers request the same default pacing.
LEGACY_TWAP_INTERVAL_SECONDS = 60

# Environment variables (CONFIG - not secrets)
ALPACA_BASE_URL = os.getenv("ALPACA_BASE_URL", "https://paper-api.alpaca.markets")
STRATEGY_ID = os.getenv("STRATEGY_ID", "alpha_baseline")
DRY_RUN = os.getenv("DRY_RUN", "true").lower() == "true"
ALPACA_PAPER = os.getenv("ALPACA_PAPER", "true").lower() == "true"
ALPACA_DATA_FEED = os.getenv("ALPACA_DATA_FEED", "").strip() or None
CIRCUIT_BREAKER_ENABLED = os.getenv("CIRCUIT_BREAKER_ENABLED", "true").lower() == "true"
LIQUIDITY_CHECK_ENABLED = os.getenv("LIQUIDITY_CHECK_ENABLED", "true").lower() in (
    "1",
    "true",
    "yes",
    "on",
)
MAX_SLICE_PCT_OF_ADV = _get_float_env("MAX_SLICE_PCT_OF_ADV", 0.01)
if MAX_SLICE_PCT_OF_ADV <= 0:
    logger.warning(
        "MAX_SLICE_PCT_OF_ADV must be > 0; falling back to default=0.01",
        extra={"max_slice_pct_of_adv": MAX_SLICE_PCT_OF_ADV},
    )
    MAX_SLICE_PCT_OF_ADV = 0.01

# Fat-finger thresholds (order size warnings)
FAT_FINGER_MAX_NOTIONAL_DEFAULT = Decimal("100000")
FAT_FINGER_MAX_QTY_DEFAULT = 10_000
FAT_FINGER_MAX_ADV_PCT_DEFAULT = Decimal("0.05")
FAT_FINGER_MAX_PRICE_AGE_SECONDS_DEFAULT = 30

_FAT_FINGER_MAX_NOTIONAL_INIT = _get_decimal_env(
    "FAT_FINGER_MAX_NOTIONAL", FAT_FINGER_MAX_NOTIONAL_DEFAULT
)
FAT_FINGER_MAX_NOTIONAL: Decimal | None = _FAT_FINGER_MAX_NOTIONAL_INIT
FAT_FINGER_MAX_QTY_RAW = os.getenv("FAT_FINGER_MAX_QTY")
FAT_FINGER_MAX_QTY: int | None
if FAT_FINGER_MAX_QTY_RAW is None:
    FAT_FINGER_MAX_QTY = FAT_FINGER_MAX_QTY_DEFAULT
else:
    try:
        FAT_FINGER_MAX_QTY = int(FAT_FINGER_MAX_QTY_RAW)
    except ValueError:
        logger.warning(
            "Invalid int for FAT_FINGER_MAX_QTY=%s; using default=%s",
            FAT_FINGER_MAX_QTY_RAW,
            FAT_FINGER_MAX_QTY_DEFAULT,
        )
        FAT_FINGER_MAX_QTY = FAT_FINGER_MAX_QTY_DEFAULT

_FAT_FINGER_MAX_ADV_PCT_INIT = _get_decimal_env(
    "FAT_FINGER_MAX_ADV_PCT", FAT_FINGER_MAX_ADV_PCT_DEFAULT
)
FAT_FINGER_MAX_ADV_PCT: Decimal | None = _FAT_FINGER_MAX_ADV_PCT_INIT

FAT_FINGER_MAX_PRICE_AGE_SECONDS_RAW = os.getenv("FAT_FINGER_MAX_PRICE_AGE_SECONDS")
if FAT_FINGER_MAX_PRICE_AGE_SECONDS_RAW is None:
    FAT_FINGER_MAX_PRICE_AGE_SECONDS = FAT_FINGER_MAX_PRICE_AGE_SECONDS_DEFAULT
else:
    try:
        FAT_FINGER_MAX_PRICE_AGE_SECONDS = int(FAT_FINGER_MAX_PRICE_AGE_SECONDS_RAW)
    except ValueError:
        logger.warning(
            "Invalid int for FAT_FINGER_MAX_PRICE_AGE_SECONDS=%s; using default=%s",
            FAT_FINGER_MAX_PRICE_AGE_SECONDS_RAW,
            FAT_FINGER_MAX_PRICE_AGE_SECONDS_DEFAULT,
        )
        FAT_FINGER_MAX_PRICE_AGE_SECONDS = FAT_FINGER_MAX_PRICE_AGE_SECONDS_DEFAULT

if FAT_FINGER_MAX_PRICE_AGE_SECONDS <= 0:
    logger.warning(
        "FAT_FINGER_MAX_PRICE_AGE_SECONDS must be > 0; using default=%s",
        FAT_FINGER_MAX_PRICE_AGE_SECONDS_DEFAULT,
    )
    FAT_FINGER_MAX_PRICE_AGE_SECONDS = FAT_FINGER_MAX_PRICE_AGE_SECONDS_DEFAULT

if _FAT_FINGER_MAX_NOTIONAL_INIT <= 0:
    logger.warning(
        "FAT_FINGER_MAX_NOTIONAL must be > 0; disabling notional threshold",
        extra={"fat_finger_max_notional": str(FAT_FINGER_MAX_NOTIONAL)},
    )
    FAT_FINGER_MAX_NOTIONAL = None

if FAT_FINGER_MAX_QTY is not None and FAT_FINGER_MAX_QTY <= 0:
    logger.warning(
        "FAT_FINGER_MAX_QTY must be > 0; disabling qty threshold",
        extra={"fat_finger_max_qty": FAT_FINGER_MAX_QTY},
    )
    FAT_FINGER_MAX_QTY = None

if _FAT_FINGER_MAX_ADV_PCT_INIT <= 0 or _FAT_FINGER_MAX_ADV_PCT_INIT > 1:
    logger.warning(
        "FAT_FINGER_MAX_ADV_PCT must be within (0, 1]; disabling ADV threshold",
        extra={"fat_finger_max_adv_pct": str(FAT_FINGER_MAX_ADV_PCT)},
    )
    FAT_FINGER_MAX_ADV_PCT = None
ENVIRONMENT = os.getenv("ENVIRONMENT", "dev")
# WEBHOOK_SECRET will be loaded in lifespan after secret validation

# Redis configuration (for real-time price lookups)
REDIS_HOST = os.getenv("REDIS_HOST", "localhost")
REDIS_PORT = int(os.getenv("REDIS_PORT", "6379"))
REDIS_DB = int(os.getenv("REDIS_DB", "0"))
# REDIS_PASSWORD will be loaded in lifespan after secret validation
PERFORMANCE_CACHE_TTL = int(os.getenv("PERFORMANCE_CACHE_TTL", "300"))
MAX_PERFORMANCE_DAYS = int(os.getenv("MAX_PERFORMANCE_DAYS", "90"))
FEATURE_PERFORMANCE_DASHBOARD = os.getenv("FEATURE_PERFORMANCE_DASHBOARD", "false").lower() in (
    "1",
    "true",
    "yes",
    "on",
)
REDUCE_ONLY_LOCK_TIMEOUT_SECONDS = int(os.getenv("REDUCE_ONLY_LOCK_TIMEOUT_SECONDS", "30"))
REDUCE_ONLY_LOCK_BLOCKING_SECONDS = int(os.getenv("REDUCE_ONLY_LOCK_BLOCKING_SECONDS", "10"))
STRATEGY_ACTIVITY_THRESHOLD_SECONDS = int(
    os.getenv("STRATEGY_ACTIVITY_THRESHOLD_SECONDS", "86400")
)  # Default 24 hours

logger.info(f"Starting Execution Gateway (version={__version__}, dry_run={DRY_RUN})")

# ============================================================================
# Initialize Clients (in lifespan after secret validation)
# ============================================================================

# Clients initialized in lifespan after secrets are validated.
# Typed as non-None because FastAPI guarantees endpoints only run after lifespan completes.
# The type:ignore[assignment] is the standard pattern for lifespan-initialized globals.
db_client: DatabaseClient = None  # type: ignore[assignment]
redis_client: RedisClient | None = None  # Can fail gracefully if Redis unavailable
alpaca_client: AlpacaExecutor | None = None
WEBHOOK_SECRET: str = ""  # Will be set in lifespan

# Liquidity service (ADV lookup for TWAP slicing)
liquidity_service: LiquidityService | None = None

# Recovery manager orchestrates safety components and slice scheduler (fail-closed)
# Initialized in lifespan; typed non-None (guaranteed when endpoints execute)
recovery_manager: RecoveryManager = None  # type: ignore[assignment]

# Risk Configuration (position limits, etc.)
risk_config = RiskConfig()
logger.info(
    f"Risk config initialized: max_position_size={risk_config.position_limits.max_position_size}"
)

# Fat-finger validator (size-based warnings/rejections)
fat_finger_validator = FatFingerValidator(
    default_thresholds=FatFingerThresholds(
        max_notional=FAT_FINGER_MAX_NOTIONAL,
        max_qty=FAT_FINGER_MAX_QTY,
        max_adv_pct=FAT_FINGER_MAX_ADV_PCT,
    )
)

# TWAP Order Slicer (stateless, no dependencies)
twap_slicer = TWAPSlicer()
logger.info("TWAP slicer initialized successfully")

# Reconciliation service (startup gating + periodic sync)
reconciliation_service: ReconciliationService | None = None
reconciliation_task: asyncio.Task[None] | None = None

# ============================================================================
# Lifespan Context Manager
# ============================================================================


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Application lifespan for startup and shutdown logic with secret management."""
    from apps.execution_gateway.api.dependencies import get_db_pool

    global db_client, redis_client, alpaca_client, WEBHOOK_SECRET
    global liquidity_service, recovery_manager, reconciliation_service, reconciliation_task

    # ========== STARTUP ==========
    logger.info("Execution Gateway started")
    logger.info(f"DRY_RUN mode: {DRY_RUN}")
    logger.info(f"Strategy ID: {STRATEGY_ID}")

    try:
        # 1. Validate required secrets BEFORE any external connections
        required = ["database/url"]
        if not DRY_RUN:
            required.extend(["alpaca/api_key_id", "alpaca/api_secret_key"])
        if ENVIRONMENT not in ("dev", "test"):
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
                host=REDIS_HOST,
                port=REDIS_PORT,
                db=REDIS_DB,
                password=redis_password,
            )
            logger.info("Redis client initialized successfully")
        except (RedisError, RedisConnectionError) as e:
            # Service should start even if Redis is misconfigured or unavailable
            logger.warning(
                f"Failed to initialize Redis client: {e}. Real-time P&L will fall back to database prices."
            )
            redis_client = None

        # Webhook secret: REQUIRED in production, optional in dev/test
        # SECURITY: In production, fail startup if webhook secret is missing
        # This prevents a running service that rejects all webhooks (missed fills)
        if ENVIRONMENT not in ("dev", "test"):
            WEBHOOK_SECRET = get_required_secret("webhook/secret")
            if not WEBHOOK_SECRET:
                raise RuntimeError(
                    "WEBHOOK_SECRET is required in production but not configured. "
                    "Set the webhook/secret in your secrets backend. "
                    "This prevents a running service that cannot receive Alpaca webhooks."
                )
        else:
            WEBHOOK_SECRET = get_optional_secret("webhook/secret", "")

        # Alpaca client and liquidity service (only if not in dry run mode)
        # Note: get_required_secret raises on missing/empty, so no additional check needed
        if not DRY_RUN:
            alpaca_api_key_id = get_required_secret("alpaca/api_key_id")
            alpaca_api_secret_key = get_required_secret("alpaca/api_secret_key")

            try:
                alpaca_client = AlpacaExecutor(
                    api_key=alpaca_api_key_id,
                    secret_key=alpaca_api_secret_key,
                    base_url=ALPACA_BASE_URL,
                    paper=ALPACA_PAPER,
                )
                logger.info("Alpaca client initialized successfully")

                # Liquidity service (ADV lookup for TWAP slicing) - reuses same credentials
                if LIQUIDITY_CHECK_ENABLED:
                    liquidity_service = LiquidityService(
                        api_key=alpaca_api_key_id,
                        api_secret=alpaca_api_secret_key,
                        data_feed=ALPACA_DATA_FEED,
                    )
                    logger.info("Liquidity service initialized successfully")
            except AlpacaConnectionError as e:
                logger.error(
                    "Failed to initialize Alpaca client",
                    extra={"error": str(e), "error_type": type(e).__name__},
                    exc_info=True,
                )
            except AlpacaValidationError as e:
                logger.error(
                    "Invalid Alpaca credentials",
                    extra={"error": str(e), "error_type": type(e).__name__},
                    exc_info=True,
                )
            except (TypeError, ValueError, KeyError) as e:
                logger.error(
                    "Configuration error initializing Alpaca services",
                    extra={"error": str(e), "error_type": type(e).__name__},
                    exc_info=True,
                )
            except OSError as e:
                logger.error(
                    "Network or I/O error initializing Alpaca services",
                    extra={"error": str(e), "error_type": type(e).__name__},
                    exc_info=True,
                )

        # Recovery manager orchestrates safety components and slice scheduler (fail-closed)
        recovery_manager = RecoveryManager(
            redis_client=redis_client,
            db_client=db_client,
            executor=alpaca_client,
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
            except TypeError as e:
                logger.error(
                    "Failed to initialize slice scheduler - invalid parameters",
                    extra={"error": str(e), "error_type": type(e).__name__},
                    exc_info=True,
                )
            except ValueError as e:
                logger.error(
                    "Failed to initialize slice scheduler - invalid configuration",
                    extra={"error": str(e), "error_type": type(e).__name__},
                    exc_info=True,
                )
            except (AttributeError, ImportError) as e:
                logger.error(
                    "Failed to initialize slice scheduler - module or attribute error",
                    extra={"error": str(e), "error_type": type(e).__name__},
                    exc_info=True,
                )
        else:
            logger.warning(
                "Slice scheduler not initialized (kill-switch or circuit-breaker unavailable)"
            )

        # Internal token check
        settings = get_settings()
        if settings.internal_token_required:
            secret_value = settings.internal_token_secret.get_secret_value()
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
        if not DRY_RUN and alpaca_client:
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
        if DRY_RUN:
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
                dry_run=DRY_RUN,
            )
            await reconciliation_service.run_startup_reconciliation()
            reconciliation_task = asyncio.create_task(reconciliation_service.run_periodic_loop())
            logger.info("Reconciliation service started")

        # Recover any pending TWAP slices after reconciliation gate opens
        asyncio.create_task(_recover_zombie_slices_after_reconciliation())

        # ========== INITIALIZE APP STATE (Phase 2B) ==========
        # Store all dependencies in app.state for Depends() pattern
        # This enables FastAPI's native dependency injection instead of factory pattern

        logger.info("Initializing app.state for dependency injection...")

        # Store version
        app.state.version = __version__

        # Store config (create from current environment variables)
        from apps.execution_gateway.config import get_config as load_config

        app.state.config = load_config()

        # Store context (create AppContext with all dependencies)
        app.state.context = AppContext(
            db=db_client,
            redis=redis_client,
            alpaca=alpaca_client,
            liquidity_service=liquidity_service,
            reconciliation_service=reconciliation_service,
            recovery_manager=recovery_manager,
            risk_config=risk_config,
            fat_finger_validator=fat_finger_validator,
            twap_slicer=twap_slicer,
            webhook_secret=WEBHOOK_SECRET,
        )

        # Store metrics (create dict for easy access via Depends())
        app.state.metrics = {
            "orders_total": orders_total,
            "order_placement_duration": order_placement_duration,
            "order_placement_duration_seconds": order_placement_duration_seconds,
            "fat_finger_warnings_total": fat_finger_warnings_total,
            "fat_finger_rejections_total": fat_finger_rejections_total,
            "positions_current": positions_current,
            "pnl_dollars": pnl_dollars,
            "database_connection_status": database_connection_status,
            "redis_connection_status": redis_connection_status,
            "alpaca_connection_status": alpaca_connection_status,
            "alpaca_api_requests_total": alpaca_api_requests_total,
            "webhook_received_total": webhook_received_total,
            "dry_run_mode": dry_run_mode,
        }

        logger.info("App.state initialized successfully")

        # ========== MOUNT ROUTERS (Phase 2) ==========
        # Mount routers after all dependencies are initialized
        # Phase 2B: Routers using Depends() are mounted at module level (see below app creation)
        # Phase 2A: Routers using factory pattern are still mounted here

        logger.info("Mounting API routers (factory pattern - Phase 2A)...")

        # NOTE: Health router now mounted at module level (Phase 2B refactoring)
        # See line ~950 for module-level router mounting

        # Admin endpoints
        admin_router = create_admin_router(
            fat_finger_validator=fat_finger_validator,
            recovery_manager=recovery_manager,
            db_client=db_client,
            environment=ENVIRONMENT,
            dry_run=DRY_RUN,
            alpaca_paper=ALPACA_PAPER,
            circuit_breaker_enabled=CIRCUIT_BREAKER_ENABLED,
            liquidity_check_enabled=LIQUIDITY_CHECK_ENABLED,
            max_slice_pct_of_adv=MAX_SLICE_PCT_OF_ADV,
            strategy_activity_threshold_seconds=STRATEGY_ACTIVITY_THRESHOLD_SECONDS,
            authenticator_getter=build_gateway_authenticator,
        )
        app.include_router(admin_router)

        # Order endpoints
        orders_router = create_orders_router(
            db_client=db_client,
            redis_client=redis_client,
            alpaca_client=alpaca_client,
            recovery_manager=recovery_manager,
            fat_finger_validator=fat_finger_validator,
            liquidity_service=liquidity_service,
            strategy_id=STRATEGY_ID,
            dry_run=DRY_RUN,
            max_realtime_price_age_seconds=FAT_FINGER_MAX_REALTIME_PRICE_AGE_SECONDS,
            order_submit_auth=order_submit_auth,
            order_submit_rl=order_submit_rl,
            order_cancel_auth=order_cancel_auth,
            order_cancel_rl=order_cancel_rl,
            order_read_auth=order_read_auth,
            orders_total=orders_total,
            order_placement_duration=order_placement_duration,
            order_placement_duration_seconds=order_placement_duration_seconds,
            fat_finger_warnings_total=fat_finger_warnings_total,
        )
        app.include_router(orders_router)

        # Slicing endpoints
        slicing_router = create_slicing_router(
            db_client=db_client,
            twap_slicer=twap_slicer,
            slice_scheduler=recovery_manager.slice_scheduler if recovery_manager else None,
            recovery_manager=recovery_manager,
            fat_finger_validator=fat_finger_validator,
            liquidity_service=liquidity_service,
            order_slice_auth=order_slice_auth,
            order_slice_rl=order_slice_rl,
            order_cancel_auth=order_cancel_auth,
            order_cancel_rl=order_cancel_rl,
        )
        app.include_router(slicing_router)

        # Position/performance endpoints
        positions_router = create_positions_router(
            db_client=db_client,
            redis_client=redis_client,
            alpaca_client=alpaca_client,
            dry_run=DRY_RUN,
            strategy_id=STRATEGY_ID,
            max_realtime_price_age_seconds=FAT_FINGER_MAX_REALTIME_PRICE_AGE_SECONDS,
            order_read_auth=order_read_auth,
            database_query_duration_seconds=database_query_duration_seconds,
            performance_cache_hit_total=performance_cache_hit_total,
            performance_cache_miss_total=performance_cache_miss_total,
        )
        app.include_router(positions_router)

        # Webhook endpoints (NO auth middleware - signature auth only)
        webhooks_router = create_webhooks_router(
            db_client=db_client,
            webhook_secret=WEBHOOK_SECRET,
        )
        app.include_router(webhooks_router)

        # NOTE: Reconciliation router now mounted at module level (Phase 2B refactoring)
        # See line ~870 for module-level router mounting

        logger.info("All API routers mounted successfully")

        yield

    finally:
        # ========== SHUTDOWN ==========
        logger.info("Execution Gateway shutting down")

        # Shutdown slice scheduler (wait for running jobs to complete)
        slice_scheduler = recovery_manager.slice_scheduler if recovery_manager else None
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
            except psycopg.OperationalError as e:
                logger.warning(
                    "Error closing async database pool - connection error",
                    extra={"error": str(e), "error_type": type(e).__name__},
                    exc_info=True,
                )
            except (RuntimeError, AttributeError) as e:
                logger.warning(
                    "Error closing async database pool - pool state error",
                    extra={"error": str(e), "error_type": type(e).__name__},
                    exc_info=True,
                )

        # Close database connection pool for clean shutdown
        if db_client:
            db_client.close()
            logger.info("Database connection pool closed")

        # Stop reconciliation task
        if reconciliation_service:
            reconciliation_service.stop()
        if reconciliation_task:
            reconciliation_task.cancel()

        # Close secret manager
        close_secret_manager()
        logger.info("Secret manager closed")


# ============================================================================
# FastAPI Application
# ============================================================================

app = FastAPI(
    title="Execution Gateway",
    description="Order execution service with idempotent submission and DRY_RUN mode",
    version=__version__,
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
)

# ============================================================================
# Module-Level Router Mounting (Phase 2B)
# ============================================================================
# Routers refactored to use Depends() pattern are mounted here at module level.
# Dependencies are injected from app.state (initialized in lifespan startup).
# This is FastAPI's idiomatic pattern - routers register routes immediately but
# handlers only execute after lifespan initializes app.state.

from apps.execution_gateway.routes import health as health_routes
from apps.execution_gateway.routes import reconciliation as reconciliation_routes

# Mount health router (Phase 2B - uses Depends() pattern)
app.include_router(health_routes.router)

# Mount reconciliation router (Phase 2B - uses Depends() pattern)
app.include_router(reconciliation_routes.router)

logger.info("Health and reconciliation routers mounted at module level (Phase 2B)")

# ============================================================================
# Proxy Headers Middleware (for accurate client IP behind load balancers)
# ============================================================================
# SECURITY: Restrict trusted_hosts to known ingress/load balancer IPs
# Never use ["*"] in production - allows IP spoofing via X-Forwarded-For
TRUSTED_PROXY_HOSTS = [
    host.strip()
    for host in os.getenv("TRUSTED_PROXY_HOSTS", "127.0.0.1").split(",")
    if host.strip()
]
app.add_middleware(ProxyHeadersMiddleware, trusted_hosts=TRUSTED_PROXY_HOSTS)  # type: ignore[arg-type]

# ============================================================================
# Rate Limiting Configuration
# ============================================================================
# Conservative limits: 80 direct + 30 slices x 3 = 170 broker orders/min (< 200 Alpaca ceiling)
ORDER_SUBMIT_LIMIT = int(os.getenv("ORDER_SUBMIT_RATE_LIMIT", "40"))
ORDER_SLICE_LIMIT = int(os.getenv("ORDER_SLICE_RATE_LIMIT", "10"))
ORDER_CANCEL_LIMIT = int(os.getenv("ORDER_CANCEL_RATE_LIMIT", "100"))  # Higher limit for safety ops
FILLS_BACKFILL_LIMIT = int(os.getenv("FILLS_BACKFILL_RATE_LIMIT", "2"))
FILLS_BACKFILL_WINDOW_SECONDS = int(
    os.getenv("FILLS_BACKFILL_RATE_LIMIT_WINDOW_SECONDS", "300")
)

order_submit_rl = rate_limit(
    RateLimitConfig(
        action="order_submit",
        max_requests=ORDER_SUBMIT_LIMIT,
        window_seconds=60,
        burst_buffer=10,
        fallback_mode="deny",
        global_limit=80,  # Direct orders only
    )
)

order_slice_rl = rate_limit(
    RateLimitConfig(
        action="order_slice",
        max_requests=ORDER_SLICE_LIMIT,
        window_seconds=60,
        burst_buffer=3,
        fallback_mode="deny",
        global_limit=30,  # 30 x 3 fan-out = 90 broker orders
    )
)

order_cancel_rl = rate_limit(
    RateLimitConfig(
        action="order_cancel",
        max_requests=ORDER_CANCEL_LIMIT,
        window_seconds=60,
        burst_buffer=20,  # Allow burst for kill-switch scenarios
        fallback_mode="allow",  # Allow cancels on Redis failure (safety-first)
        global_limit=200,  # Higher global for emergency cancellations
    )
)

reconciliation_fills_backfill_rl = rate_limit(
    RateLimitConfig(
        action="fills_backfill",
        max_requests=FILLS_BACKFILL_LIMIT,
        window_seconds=FILLS_BACKFILL_WINDOW_SECONDS,
        burst_buffer=1,
        fallback_mode="deny",
        global_limit=FILLS_BACKFILL_LIMIT,
    )
)

# ============================================================================
# API Authentication Configuration (C6)
# ============================================================================
# Auth dependencies for trading endpoints. Defaults to enforce mode (fail-closed).
# Set API_AUTH_MODE=log_only for staged rollout.

# Import authenticator builder for JWT validation (injected to avoid layering violation)
from apps.execution_gateway.api.dependencies import build_gateway_authenticator  # noqa: E402

order_submit_auth = api_auth(
    APIAuthConfig(
        action="order_submit",
        require_role=None,  # Role checked via permission
        require_permission=Permission.SUBMIT_ORDER,
    ),
    authenticator_getter=build_gateway_authenticator,
)

order_slice_auth = api_auth(
    APIAuthConfig(
        action="order_slice",
        require_role=None,
        require_permission=Permission.SUBMIT_ORDER,
    ),
    authenticator_getter=build_gateway_authenticator,
)

order_cancel_auth = api_auth(
    APIAuthConfig(
        action="order_cancel",
        require_role=None,
        require_permission=Permission.CANCEL_ORDER,
    ),
    authenticator_getter=build_gateway_authenticator,
)

order_read_auth = api_auth(
    APIAuthConfig(
        action="order_read",
        require_role=None,
        require_permission=Permission.VIEW_POSITIONS,
    ),
    authenticator_getter=build_gateway_authenticator,
)

kill_switch_auth = api_auth(
    APIAuthConfig(
        action="kill_switch",
        require_role=None,
        require_permission=Permission.CANCEL_ORDER,
    ),
    authenticator_getter=build_gateway_authenticator,
)

app.include_router(manual_controls_router, prefix="/api/v1", tags=["Manual Controls"])

# ============================================================================
# Proxy Headers Middleware

# ============================================================================
# Prometheus Metrics
# ============================================================================

# Business Metrics
orders_total = Counter(
    "execution_gateway_orders_total",
    "Total number of orders submitted",
    ["symbol", "side", "status"],  # status: success, failed, rejected, blocked
)

order_placement_duration = Histogram(
    "execution_gateway_order_placement_duration_seconds",
    "Time taken to place an order",
    ["symbol", "side"],
)

# Latency histogram for shared health dashboard (no service prefix)
order_placement_duration_seconds = Histogram(
    "order_placement_duration_seconds",
    "Time taken to place an order",
)

fat_finger_warnings_total = Counter(
    "execution_gateway_fat_finger_warnings_total",
    "Total fat-finger threshold warnings",
    ["threshold_type"],
)

fat_finger_rejections_total = Counter(
    "execution_gateway_fat_finger_rejections_total",
    "Total fat-finger threshold rejections",
    ["threshold_type"],
)

positions_current = Gauge(
    "execution_gateway_positions_current",
    "Current open positions by symbol",
    ["symbol"],
)

pnl_dollars = Gauge(
    "execution_gateway_pnl_dollars",
    "P&L in dollars",
    ["type"],  # Label values: realized, unrealized, total
)

# Service Health Metrics
database_connection_status = Gauge(
    "execution_gateway_database_connection_status",
    "Database connection status (1=up, 0=down)",
)

redis_connection_status = Gauge(
    "execution_gateway_redis_connection_status",
    "Redis connection status (1=up, 0=down)",
)

alpaca_connection_status = Gauge(
    "execution_gateway_alpaca_connection_status",
    "Alpaca connection status (1=up, 0=down)",
)

alpaca_api_requests_total = Counter(
    "execution_gateway_alpaca_api_requests_total",
    "Total Alpaca API requests",
    ["operation", "status"],  # operation: submit_order, check_connection; status: success, error
)

webhook_received_total = Counter(
    "execution_gateway_webhook_received_total",
    "Total webhooks received",
    ["event_type"],
)

dry_run_mode = Gauge(
    "execution_gateway_dry_run_mode",
    "DRY_RUN mode status (1=enabled, 0=disabled)",
)

# Set initial metric values
dry_run_mode.set(1 if DRY_RUN else 0)
database_connection_status.set(0)  # Will be updated by health check
redis_connection_status.set(0)  # Will be updated by health check
alpaca_connection_status.set(0)  # Will be updated by health check

# Mount Prometheus metrics endpoint
metrics_app = make_asgi_app()
app.mount("/metrics", metrics_app)

# Track symbols we've set position metrics for (to reset when positions close)
_tracked_position_symbols: set[str] = set()
_position_metrics_lock = asyncio.Lock()


# ============================================================================
# Helper Functions
# ============================================================================


def _record_order_metrics(
    order: "OrderRequest",
    start_time: float,
    status: Literal["success", "rejected", "failed", "blocked"],
) -> None:
    """
    Record Prometheus metrics for order placement.

    Args:
        order: The order request that was submitted
        start_time: Time when order processing started (from time.time())
        status: Order outcome (success, rejected, failed, or blocked)

    Notes:
        This helper reduces code duplication across different order placement paths.
        Increments orders_total counter and records order_placement_duration histogram.
        "blocked" status is used for orders rejected by safety mechanisms (circuit breaker).
    """
    duration = time.time() - start_time
    orders_total.labels(symbol=order.symbol, side=order.side, status=status).inc()
    order_placement_duration.labels(symbol=order.symbol, side=order.side).observe(duration)
    order_placement_duration_seconds.observe(duration)


def handle_idempotency_race(
    client_order_id: str,
    db_client: "DatabaseClient",
) -> "OrderResponse":
    """
    Handle idempotency race condition by returning existing order.

    When UniqueViolation is caught during order creation, this function
    retrieves the existing order and returns an idempotent response.

    Args:
        client_order_id: The client order ID that caused the race condition
        db_client: Database client for fetching existing order

    Returns:
        OrderResponse for the existing order

    Raises:
        HTTPException: If order not found after UniqueViolation (should never happen)
    """
    logger.info(
        f"Concurrent order submission detected (UniqueViolation): {client_order_id}",
        extra={"client_order_id": client_order_id},
    )
    existing_order = db_client.get_order_by_client_id(client_order_id)
    if existing_order:
        return OrderResponse(
            client_order_id=client_order_id,
            status=existing_order.status,
            broker_order_id=existing_order.broker_order_id,
            symbol=existing_order.symbol,
            side=existing_order.side,
            qty=existing_order.qty,
            order_type=existing_order.order_type,
            limit_price=existing_order.limit_price,
            created_at=existing_order.created_at,
            message="Order already submitted (race condition resolved)",
        )
    # Should never happen: UniqueViolation means order exists
    raise HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail="Database inconsistency: order not found after UniqueViolation",
    )


def parse_webhook_timestamp(*timestamps: Any, default: datetime) -> datetime:
    """Parse the first valid timestamp from a list of candidates.

    Iterates through the provided timestamp candidates and returns the first
    one that can be successfully parsed. Falls back to the default if none
    are valid.

    Args:
        *timestamps: Variable number of timestamp candidates (str, datetime, or None)
        default: Fallback datetime if no valid timestamp is found

    Returns:
        Parsed datetime or the default value
    """
    for ts in timestamps:
        if not ts:
            continue
        if isinstance(ts, datetime):
            return ts
        try:
            return datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        except (ValueError, TypeError):
            continue
    return default


async def resolve_fat_finger_context(
    order: OrderRequest,
    thresholds: FatFingerThresholds,
) -> tuple[Decimal | None, int | None]:
    """Resolve price and ADV context needed for fat-finger validation.

    Uses asyncio.to_thread for ADV lookup to avoid blocking the event loop.
    """

    price: Decimal | None = None
    if thresholds.max_notional is not None:
        if order.limit_price is not None:
            price = order.limit_price
        elif order.stop_price is not None:
            price = order.stop_price
        else:
            realtime_prices = batch_fetch_realtime_prices_from_redis([order.symbol], redis_client)
            price, price_timestamp = realtime_prices.get(order.symbol, (None, None))
            if price is not None:
                if price_timestamp is None:
                    logger.warning(
                        "Fat-finger price missing timestamp; treating as unavailable",
                        extra={
                            "symbol": order.symbol,
                            "max_price_age_seconds": FAT_FINGER_MAX_PRICE_AGE_SECONDS,
                        },
                    )
                    price = None
                else:
                    if price_timestamp.tzinfo is None:
                        price_timestamp = price_timestamp.replace(tzinfo=UTC)
                    now = datetime.now(UTC)
                    price_age_seconds = (now - price_timestamp).total_seconds()
                    if price_age_seconds > FAT_FINGER_MAX_PRICE_AGE_SECONDS:
                        logger.warning(
                            "Fat-finger price stale; treating as unavailable",
                            extra={
                                "symbol": order.symbol,
                                "price_timestamp": price_timestamp.isoformat(),
                                "price_age_seconds": max(price_age_seconds, 0),
                                "max_price_age_seconds": FAT_FINGER_MAX_PRICE_AGE_SECONDS,
                            },
                        )
                        price = None

    adv: int | None = None
    if thresholds.max_adv_pct is not None and liquidity_service is not None:
        adv = await asyncio.to_thread(liquidity_service.get_adv, order.symbol)

    return price, adv


def create_fat_finger_thresholds_snapshot() -> FatFingerThresholdsResponse:
    """Build a response payload with current fat-finger thresholds."""

    return FatFingerThresholdsResponse(
        default_thresholds=fat_finger_validator.get_default_thresholds(),
        symbol_overrides=fat_finger_validator.get_symbol_overrides(),
        updated_at=datetime.now(UTC),
    )


def _is_reconciliation_ready() -> bool:
    """Return True when startup reconciliation gate is open."""
    if DRY_RUN:
        return True
    if reconciliation_service is None:
        return False
    return reconciliation_service.is_startup_complete()


async def _recover_zombie_slices_after_reconciliation() -> None:
    """Recover pending TWAP slices after reconciliation gate opens."""
    if not recovery_manager:
        logger.warning("Recovery manager unavailable; skipping zombie slice recovery")
        return
    slice_scheduler = recovery_manager.slice_scheduler
    if not slice_scheduler:
        logger.warning("Slice scheduler unavailable; skipping zombie slice recovery")
        return
    if not DRY_RUN and reconciliation_service is None:
        logger.error("Reconciliation service unavailable; skipping zombie slice recovery")
        return

    poll_interval_seconds = 1.0
    while not _is_reconciliation_ready():
        if reconciliation_service and reconciliation_service.startup_timed_out():
            logger.error("Startup reconciliation timed out; skipping zombie slice recovery")
            return
        await asyncio.sleep(poll_interval_seconds)

    await asyncio.to_thread(slice_scheduler.recover_zombie_slices)


async def _check_quarantine(symbol: str, strategy_id: str) -> None:
    """Block trading when symbol is quarantined."""
    if DRY_RUN:
        return
    if not redis_client:
        logger.error(
            "Redis unavailable for quarantine check; failing closed",
            extra={"symbol": symbol},
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "error": "Quarantine check unavailable",
                "message": "Redis unavailable for quarantine enforcement (fail-closed).",
            },
        )

    try:
        symbol = symbol.upper()
        strategy_key = RedisKeys.quarantine(strategy_id=strategy_id, symbol=symbol)
        wildcard_key = RedisKeys.quarantine(strategy_id="*", symbol=symbol)
        values = await asyncio.to_thread(redis_client.mget, [strategy_key, wildcard_key])
        strategy_value, wildcard_value = (values + [None, None])[:2] if values else (None, None)
        if strategy_value or wildcard_value:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={
                    "error": "Symbol quarantined",
                    "message": f"Trading blocked for {symbol} due to orphan order quarantine",
                    "symbol": symbol,
                },
            )
    except HTTPException:
        raise
    except RedisError as exc:
        logger.error(
            "Quarantine check failed - Redis error",
            extra={"symbol": symbol, "error": str(exc), "error_type": type(exc).__name__},
            exc_info=True,
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "error": "Quarantine check unavailable",
                "message": "Redis unavailable for quarantine enforcement (fail-closed).",
            },
        ) from exc
    except redis.exceptions.ConnectionError as exc:
        logger.error(
            "Quarantine check failed - Redis connection error",
            extra={"symbol": symbol, "error": str(exc)},
            exc_info=True,
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "error": "Quarantine check unavailable",
                "message": "Redis connection unavailable for quarantine enforcement (fail-closed).",
            },
        ) from exc
    except (TypeError, KeyError, AttributeError) as exc:
        logger.error(
            "Quarantine check failed - data access error",
            extra={"symbol": symbol, "error": str(exc), "error_type": type(exc).__name__},
            exc_info=True,
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "error": "Quarantine check unavailable",
                "message": "Data structure error during quarantine check (fail-closed).",
            },
        ) from exc


async def _require_reconciliation_ready_or_reduce_only(order: OrderRequest) -> None:
    """Gate order submissions until reconciliation completes (reduce-only allowed)."""
    if reconciliation_service and reconciliation_service.override_active():
        logger.warning(
            "Reconciliation override active; allowing order",
            extra={
                "client_order_id": generate_client_order_id(order, STRATEGY_ID),
                "override": reconciliation_service.override_context(),
            },
        )
        return

    if _is_reconciliation_ready():
        return

    if reconciliation_service and reconciliation_service.startup_timed_out():
        logger.critical(
            "Startup reconciliation timed out; remaining in gated mode",
            extra={"timeout_seconds": reconciliation_service.timeout_seconds},
        )

    await _enforce_reduce_only_order(order)


async def _enforce_reduce_only_order(order: OrderRequest) -> None:
    """Allow only reduce-only orders during reconciliation gating."""
    if not alpaca_client:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Broker client unavailable during reconciliation gating",
        )

    if not redis_client:
        logger.error(
            "Redis unavailable for reduce-only lock; failing closed",
            extra={"symbol": order.symbol},
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "error": "Reduce-only lock unavailable",
                "message": "Redis unavailable for reduce-only validation (fail-closed).",
            },
        )

    lock_key = RedisKeys.reduce_only_lock(order.symbol.upper())
    lock = redis_client.lock(
        lock_key,
        timeout=REDUCE_ONLY_LOCK_TIMEOUT_SECONDS,
        blocking_timeout=REDUCE_ONLY_LOCK_BLOCKING_SECONDS,
    )
    acquired = False
    try:
        acquired = await asyncio.to_thread(lock.acquire, blocking=True)
    except redis.exceptions.LockError as exc:
        logger.error(
            "Failed to acquire reduce-only lock - lock error",
            extra={"symbol": order.symbol, "error": str(exc)},
            exc_info=True,
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "error": "Reduce-only lock unavailable",
                "message": "Unable to acquire reduce-only validation lock (fail-closed).",
            },
        ) from exc
    except RedisError as exc:
        logger.error(
            "Failed to acquire reduce-only lock - Redis error",
            extra={"symbol": order.symbol, "error": str(exc), "error_type": type(exc).__name__},
            exc_info=True,
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "error": "Reduce-only lock unavailable",
                "message": "Unable to acquire reduce-only validation lock (fail-closed).",
            },
        ) from exc
    except TimeoutError as exc:
        logger.error(
            "Failed to acquire reduce-only lock - timeout",
            extra={"symbol": order.symbol, "error": str(exc), "error_type": type(exc).__name__},
            exc_info=True,
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "error": "Reduce-only lock unavailable",
                "message": "Timeout acquiring reduce-only validation lock (fail-closed).",
            },
        ) from exc

    if not acquired:
        logger.error(
            "Reduce-only lock acquisition timed out",
            extra={"symbol": order.symbol, "lock_key": lock_key},
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "error": "Reduce-only lock timeout",
                "message": "Reduce-only validation lock could not be acquired in time.",
            },
        )

    try:
        position = await asyncio.to_thread(alpaca_client.get_open_position, order.symbol)
        # Filter by symbol at API level for efficiency
        open_orders = await asyncio.to_thread(
            alpaca_client.get_orders, status="open", limit=500, after=None, symbols=[order.symbol]
        )

        current_position = Decimal("0")
        if position:
            current_position = Decimal(str(position.get("qty") or 0))

        open_buy_qty = Decimal("0")
        open_sell_qty = Decimal("0")
        for open_order in open_orders:
            qty = Decimal(str(open_order.get("qty") or 0))
            filled_qty = Decimal(str(open_order.get("filled_qty") or 0))
            remaining = qty - filled_qty
            if remaining <= 0:
                continue
            if open_order.get("side") == "buy":
                open_buy_qty += remaining
            elif open_order.get("side") == "sell":
                open_sell_qty += remaining

        # Calculate effective position including pending orders
        effective_position = current_position + open_buy_qty - open_sell_qty

        if effective_position == 0:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={
                    "error": "Reduce-only required",
                    "message": "No position available to reduce during reconciliation",
                },
            )

        # Calculate projected position after this order
        side_multiplier = Decimal("1") if order.side == "buy" else Decimal("-1")
        projected_position = effective_position + (Decimal(order.qty) * side_multiplier)

        # An order is position-increasing if it moves the position's
        # absolute value further from zero
        is_increasing = abs(projected_position) > abs(effective_position)
        # An order flips the position if the sign changes
        is_flipping = effective_position * projected_position < 0

        if is_increasing or is_flipping:
            error_message = (
                "Only position-reducing orders are allowed during " "reconciliation gating."
            )
            if is_flipping:
                error_message = "Order would flip position during reconciliation gating."

            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={
                    "error": "Reduce-only required",
                    "message": error_message,
                },
            )

        logger.info(
            "Allowing reduce-only order during reconciliation gating",
            extra={
                "symbol": order.symbol,
                "side": order.side,
                "qty": order.qty,
                "effective_position": str(effective_position),
                "projected_position": str(projected_position),
            },
        )
    except AlpacaConnectionError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "error": "Broker connection error",
                "message": "Cannot evaluate reduce-only order during reconciliation gating",
            },
        ) from exc
    finally:
        if acquired and lock.locked():
            try:
                await asyncio.to_thread(lock.release)
            except redis.exceptions.LockError as exc:
                logger.warning(
                    "Failed to release reduce-only lock - lock error",
                    extra={"symbol": order.symbol, "error": str(exc)},
                )
            except RedisError as exc:
                logger.warning(
                    "Failed to release reduce-only lock - Redis error",
                    extra={"symbol": order.symbol, "error": str(exc), "error_type": type(exc).__name__},
                )


# ============================================================================
# Exception Handlers
# ============================================================================


@app.exception_handler(ValidationError)
async def validation_exception_handler(request: Request, exc: ValidationError) -> JSONResponse:
    """Handle Pydantic validation errors."""
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content=ErrorResponse(
            error="Validation error", detail=str(exc), timestamp=datetime.now(UTC)
        ).model_dump(mode="json"),
    )


@app.exception_handler(PermissionError)
async def permission_exception_handler(request: Request, exc: PermissionError) -> JSONResponse:
    """Map RBAC PermissionError to HTTP 403 for API clients."""

    return JSONResponse(
        status_code=status.HTTP_403_FORBIDDEN,
        content=ErrorResponse(
            error="Forbidden", detail=str(exc) or "Permission denied", timestamp=datetime.now(UTC)
        ).model_dump(mode="json"),
    )


@app.exception_handler(AlpacaValidationError)
async def alpaca_validation_handler(request: Request, exc: AlpacaValidationError) -> JSONResponse:
    """Handle Alpaca validation errors."""
    return JSONResponse(
        status_code=status.HTTP_400_BAD_REQUEST,
        content=ErrorResponse(
            error="Order validation failed", detail=str(exc), timestamp=datetime.now(UTC)
        ).model_dump(mode="json"),
    )


@app.exception_handler(AlpacaRejectionError)
async def alpaca_rejection_handler(request: Request, exc: AlpacaRejectionError) -> JSONResponse:
    """Handle Alpaca order rejection errors."""
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content=ErrorResponse(
            error="Order rejected by broker", detail=str(exc), timestamp=datetime.now(UTC)
        ).model_dump(mode="json"),
    )


@app.exception_handler(AlpacaConnectionError)
async def alpaca_connection_handler(request: Request, exc: AlpacaConnectionError) -> JSONResponse:
    """Handle Alpaca connection errors."""
    return JSONResponse(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        content=ErrorResponse(
            error="Broker connection error", detail=str(exc), timestamp=datetime.now(UTC)
        ).model_dump(mode="json"),
    )



# ============================================================================
# Startup / Shutdown
# ============================================================================


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "apps.execution_gateway.main:app",
        host="0.0.0.0",
        port=8002,
        reload=True,
        log_level=LOG_LEVEL.lower(),
    )

"""
Execution Gateway - Order execution service with idempotent submission.

See ADR-0014 for architecture decisions.
"""

import asyncio
import json
import logging
import os
import time
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any, cast

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response, status
from fastapi.responses import JSONResponse
from prometheus_client import Counter, Gauge, Histogram, make_asgi_app
from pydantic import ValidationError
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
from apps.execution_gateway.lifespan import (
    LifespanResources,
    LifespanSettings,
    shutdown_execution_gateway,
    startup_execution_gateway,
)
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

# Re-exports for external consumers
from apps.execution_gateway.app_context import AppContext  # noqa: F401
from apps.execution_gateway.app_factory import create_app  # noqa: F401
from apps.execution_gateway.config import ExecutionGatewayConfig, get_config  # noqa: F401
from apps.execution_gateway.dependencies import get_config as get_config_dependency, get_context  # noqa: F401

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

from libs.core.redis_client import RedisClient
from libs.trading.risk_management import CircuitBreaker, KillSwitch, PositionReservation, RiskConfig

# Route modules
from apps.execution_gateway.routes import admin as admin_routes
from apps.execution_gateway.routes import orders as orders_routes
from apps.execution_gateway.routes import positions as positions_routes
from apps.execution_gateway.routes import slicing as slicing_routes
from apps.execution_gateway.routes import webhooks as webhooks_routes


# =============================================================================
# Configuration
# =============================================================================

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
logging.basicConfig(level=LOG_LEVEL, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)


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


# Legacy TWAP interval for backward-compatible slice scheduling
LEGACY_TWAP_INTERVAL_SECONDS = 60

# Environment config (secrets loaded in lifespan)
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

# Fat-finger thresholds
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

# Redis config
REDIS_HOST = os.getenv("REDIS_HOST", "localhost")
REDIS_PORT = int(os.getenv("REDIS_PORT", "6379"))
REDIS_DB = int(os.getenv("REDIS_DB", "0"))
PERFORMANCE_CACHE_TTL = int(os.getenv("PERFORMANCE_CACHE_TTL", "300"))
MAX_PERFORMANCE_DAYS = int(os.getenv("MAX_PERFORMANCE_DAYS", "90"))
FEATURE_PERFORMANCE_DASHBOARD = os.getenv("FEATURE_PERFORMANCE_DASHBOARD", "false").lower() in (
    "1",
    "true",
    "yes",
    "on",
)
STRATEGY_ACTIVITY_THRESHOLD_SECONDS = int(
    os.getenv("STRATEGY_ACTIVITY_THRESHOLD_SECONDS", "86400")
)  # Default 24 hours

logger.info(f"Starting Execution Gateway (version={__version__}, dry_run={DRY_RUN})")

# =============================================================================
# Globals (initialized in lifespan)
# =============================================================================

# Clients - type:ignore for lifespan-initialized globals (FastAPI pattern)
db_client: DatabaseClient = None  # type: ignore[assignment]
redis_client: RedisClient | None = None
alpaca_client: AlpacaExecutor | None = None
WEBHOOK_SECRET: str = ""
liquidity_service: LiquidityService | None = None
recovery_manager: RecoveryManager = None  # type: ignore[assignment]
reconciliation_service: ReconciliationService | None = None
reconciliation_task: asyncio.Task[None] | None = None

# Stateless components (initialized at import time)
risk_config = RiskConfig()
logger.info(f"Risk config initialized: max_position_size={risk_config.position_limits.max_position_size}")

fat_finger_validator = FatFingerValidator(
    default_thresholds=FatFingerThresholds(
        max_notional=FAT_FINGER_MAX_NOTIONAL,
        max_qty=FAT_FINGER_MAX_QTY,
        max_adv_pct=FAT_FINGER_MAX_ADV_PCT,
    )
)

twap_slicer = TWAPSlicer()
logger.info("TWAP slicer initialized successfully")

# =============================================================================
# Lifespan
# =============================================================================


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Application lifespan for startup and shutdown logic with secret management."""
    global db_client, redis_client, alpaca_client, WEBHOOK_SECRET
    global liquidity_service, recovery_manager, reconciliation_service, reconciliation_task

    settings = LifespanSettings(
        dry_run=DRY_RUN,
        strategy_id=STRATEGY_ID,
        environment=ENVIRONMENT,
        alpaca_base_url=ALPACA_BASE_URL,
        alpaca_paper=ALPACA_PAPER,
        alpaca_data_feed=ALPACA_DATA_FEED,
        liquidity_check_enabled=LIQUIDITY_CHECK_ENABLED,
        redis_host=REDIS_HOST,
        redis_port=REDIS_PORT,
        redis_db=REDIS_DB,
        version=__version__,
        risk_config=risk_config,
        fat_finger_validator=fat_finger_validator,
        twap_slicer=twap_slicer,
    )

    resources: LifespanResources | None = None
    try:
        resources = await startup_execution_gateway(
            app, settings=settings, metrics=_build_metrics()
        )

        db_client = resources.db_client
        redis_client = resources.redis_client
        alpaca_client = resources.alpaca_client
        WEBHOOK_SECRET = resources.webhook_secret
        liquidity_service = resources.liquidity_service
        recovery_manager = resources.recovery_manager
        reconciliation_service = resources.reconciliation_service
        reconciliation_task = resources.reconciliation_task

        yield
    finally:
        if resources is not None:
            await shutdown_execution_gateway(resources)


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

# =============================================================================
# Router Mounting
# =============================================================================

from apps.execution_gateway.routes import health as health_routes
from apps.execution_gateway.routes import reconciliation as reconciliation_routes

app.include_router(health_routes.router)
app.include_router(reconciliation_routes.router)
app.include_router(admin_routes.router)
app.include_router(webhooks_routes.router)  # Uses signature auth, not bearer token
app.include_router(positions_routes.router)
app.include_router(orders_routes.router)
app.include_router(slicing_routes.router)

logger.info("All routers mounted")

# Proxy headers middleware (restrict trusted_hosts in production to prevent IP spoofing)
TRUSTED_PROXY_HOSTS = [h.strip() for h in os.getenv("TRUSTED_PROXY_HOSTS", "127.0.0.1").split(",") if h.strip()]
app.add_middleware(ProxyHeadersMiddleware, trusted_hosts=TRUSTED_PROXY_HOSTS)  # type: ignore[arg-type]
app.middleware("http")(populate_user_from_headers_middleware)

app.include_router(manual_controls_router, prefix="/api/v1", tags=["Manual Controls"])

# =============================================================================
# Prometheus Metrics
# =============================================================================

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
    ["operation", "status"],
)

from apps.execution_gateway.routes.webhooks import webhook_received_total  # noqa: E402

dry_run_mode = Gauge(
    "execution_gateway_dry_run_mode",
    "DRY_RUN mode status (1=enabled, 0=disabled)",
)

dry_run_mode.set(1 if DRY_RUN else 0)
database_connection_status.set(0)
redis_connection_status.set(0)
alpaca_connection_status.set(0)

metrics_app = make_asgi_app()
app.mount("/metrics", metrics_app)


def _build_metrics() -> dict[str, Any]:
    """Build metrics mapping for app.state."""
    return {
        "orders_total": orders_total,
        "order_placement_duration": order_placement_duration,
        "order_placement_duration_seconds": order_placement_duration_seconds,
        "fat_finger_warnings_total": fat_finger_warnings_total,
        "fat_finger_rejections_total": fat_finger_rejections_total,
        "positions_current": positions_routes.positions_current,
        "pnl_dollars": positions_routes.pnl_dollars,
        "database_connection_status": database_connection_status,
        "redis_connection_status": redis_connection_status,
        "alpaca_connection_status": alpaca_connection_status,
        "alpaca_api_requests_total": alpaca_api_requests_total,
        "webhook_received_total": webhook_received_total,
        "dry_run_mode": dry_run_mode,
    }


# =============================================================================
# Exception Handlers
# =============================================================================


@app.exception_handler(ValidationError)
async def validation_exception_handler(request: Request, exc: ValidationError) -> JSONResponse:
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content=ErrorResponse(error="Validation error", detail=str(exc), timestamp=datetime.now(UTC)).model_dump(mode="json"),
    )


@app.exception_handler(PermissionError)
async def permission_exception_handler(request: Request, exc: PermissionError) -> JSONResponse:
    return JSONResponse(
        status_code=status.HTTP_403_FORBIDDEN,
        content=ErrorResponse(error="Forbidden", detail=str(exc) or "Permission denied", timestamp=datetime.now(UTC)).model_dump(mode="json"),
    )


@app.exception_handler(AlpacaValidationError)
async def alpaca_validation_handler(request: Request, exc: AlpacaValidationError) -> JSONResponse:
    return JSONResponse(
        status_code=status.HTTP_400_BAD_REQUEST,
        content=ErrorResponse(error="Order validation failed", detail=str(exc), timestamp=datetime.now(UTC)).model_dump(mode="json"),
    )


@app.exception_handler(AlpacaRejectionError)
async def alpaca_rejection_handler(request: Request, exc: AlpacaRejectionError) -> JSONResponse:
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content=ErrorResponse(error="Order rejected by broker", detail=str(exc), timestamp=datetime.now(UTC)).model_dump(mode="json"),
    )


@app.exception_handler(AlpacaConnectionError)
async def alpaca_connection_handler(request: Request, exc: AlpacaConnectionError) -> JSONResponse:
    return JSONResponse(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        content=ErrorResponse(error="Broker connection error", detail=str(exc), timestamp=datetime.now(UTC)).model_dump(mode="json"),
    )


# =============================================================================
# Entrypoint
# =============================================================================


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "apps.execution_gateway.main:app",
        host="0.0.0.0",
        port=8002,
        reload=True,
        log_level=LOG_LEVEL.lower(),
    )

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
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any, Literal, Protocol, cast

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
    OrderDetail,
    OrderRequest,
    OrderResponse,
    PerformanceRequest,
    Position,
    PositionsResponse,
    RealtimePnLResponse,
    RealtimePositionPnL,
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
from config.settings import get_settings
from libs.common.api_auth_dependency import (
    APIAuthConfig,
    AuthContext,
    api_auth,
)
from libs.common.rate_limit_dependency import RateLimitConfig, rate_limit
from libs.redis_client import RedisClient, RedisConnectionError, RedisKeys
from libs.risk_management import (
    CircuitBreaker,
    KillSwitch,
    PositionReservation,
    RiskConfig,
)

# DESIGN DECISION: Shared auth library in libs/ instead of importing from apps.web_console.
# This prevents backend→frontend dependency while sharing RBAC logic across services.
# Alternative: Import from apps.web_console with runtime guards, rejected due to circular
# dependency risk and tight coupling between frontend/backend deployment cycles.
from libs.web_console_auth.permissions import (
    Permission,
    get_authorized_strategies,
    has_permission,
    require_permission,
)

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

# Environment variables
ALPACA_API_KEY_ID = os.getenv("ALPACA_API_KEY_ID", "")
ALPACA_API_SECRET_KEY = os.getenv("ALPACA_API_SECRET_KEY", "")
ALPACA_BASE_URL = os.getenv("ALPACA_BASE_URL", "https://paper-api.alpaca.markets")
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://trader:trader@localhost:5433/trader")
STRATEGY_ID = os.getenv("STRATEGY_ID", "alpha_baseline")
DRY_RUN = os.getenv("DRY_RUN", "true").lower() == "true"
ALPACA_PAPER = os.getenv("ALPACA_PAPER", "true").lower() == "true"
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
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "")  # Secret for webhook signature verification

# C3 Fix: Validate webhook secret is set in production environments
# In non-dev/test environments with DRY_RUN=false, webhook secret is MANDATORY
# This prevents webhook spoofing attacks in production
# Strip whitespace to prevent whitespace-only secrets (which would be useless)
WEBHOOK_SECRET = WEBHOOK_SECRET.strip()
if not WEBHOOK_SECRET and ENVIRONMENT not in ("dev", "test") and not DRY_RUN:
    raise RuntimeError(
        "WEBHOOK_SECRET must be set for production/staging environments. "
        "Set WEBHOOK_SECRET environment variable or use DRY_RUN=true for testing."
    )

# Redis configuration (for real-time price lookups)
REDIS_HOST = os.getenv("REDIS_HOST", "localhost")
REDIS_PORT = int(os.getenv("REDIS_PORT", "6379"))
REDIS_DB = int(os.getenv("REDIS_DB", "0"))
REDIS_PASSWORD = os.getenv("REDIS_PASSWORD", None)
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
# Initialize Clients
# ============================================================================

# Database client
db_client = DatabaseClient(DATABASE_URL)

# Redis client (for real-time price lookups from Market Data Service)
redis_client: RedisClient | None = None
try:
    redis_client = RedisClient(
        host=REDIS_HOST,
        port=REDIS_PORT,
        db=REDIS_DB,
        password=REDIS_PASSWORD,
    )
    logger.info("Redis client initialized successfully")
except (RedisError, RedisConnectionError) as e:
    # Catch both redis-py errors (RedisError) and our custom RedisConnectionError
    # Service should start even if Redis is misconfigured or unavailable
    # RedisConnectionError is raised by RedisClient when initial ping() fails
    logger.warning(
        f"Failed to initialize Redis client: {e}. Real-time P&L will fall back to database prices."
    )

# Alpaca client (only if not in dry run mode and credentials provided)
alpaca_client: AlpacaExecutor | None = None
if not DRY_RUN:
    if not ALPACA_API_KEY_ID or not ALPACA_API_SECRET_KEY:
        logger.warning(
            "DRY_RUN=false but Alpaca credentials not provided. "
            "Set ALPACA_API_KEY_ID and ALPACA_API_SECRET_KEY environment variables."
        )
    else:
        try:
            alpaca_client = AlpacaExecutor(
                api_key=ALPACA_API_KEY_ID,
                secret_key=ALPACA_API_SECRET_KEY,
                base_url=ALPACA_BASE_URL,
                paper=True,
            )
            logger.info("Alpaca client initialized successfully")
        except Exception as e:
            logger.error(f"Failed to initialize Alpaca client: {e}")

# Liquidity service (ADV lookup for TWAP slicing)
liquidity_service: LiquidityService | None = None
if LIQUIDITY_CHECK_ENABLED:
    try:
        liquidity_service = LiquidityService(
            api_key=ALPACA_API_KEY_ID,
            api_secret=ALPACA_API_SECRET_KEY,
        )
        logger.info("Liquidity service initialized successfully")
    except Exception as e:
        logger.error(f"Failed to initialize Liquidity service: {e}")

# Recovery manager orchestrates safety components and slice scheduler (fail-closed)
recovery_manager = RecoveryManager(
    redis_client=redis_client,
    db_client=db_client,
    executor=alpaca_client,
)

# Initialize safety components (fail-closed on any error)
# Note: Factories only called when redis_client is verified available by RecoveryManager
recovery_manager.initialize_kill_switch(
    lambda: KillSwitch(redis_client=redis_client)  # type: ignore[arg-type]
)
recovery_manager.initialize_circuit_breaker(
    lambda: CircuitBreaker(redis_client=redis_client)  # type: ignore[arg-type]
)
recovery_manager.initialize_position_reservation(
    lambda: PositionReservation(redis=redis_client)  # type: ignore[arg-type]
)

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

# Position Reservation (for atomic position limit checking - prevents race conditions)
# Codex HIGH fix: Wire PositionReservation into production to prevent concurrent orders
# from both passing position limit check before either executes

# TWAP Order Slicer (stateless, no dependencies)
twap_slicer = TWAPSlicer()
logger.info("TWAP slicer initialized successfully")

# Slice Scheduler (for time-based TWAP slice execution)
if recovery_manager.kill_switch and recovery_manager.circuit_breaker:
    # Note: alpaca_client can be None in DRY_RUN mode - scheduler logs
    # dry-run slices without broker submission
    try:
        recovery_manager.slice_scheduler = SliceScheduler(
            kill_switch=recovery_manager.kill_switch,
            breaker=recovery_manager.circuit_breaker,
            db_client=db_client,
            executor=alpaca_client,  # Can be None in DRY_RUN mode
        )
        logger.info("Slice scheduler initialized (not started yet)")
    except Exception as e:
        logger.error(f"Failed to initialize slice scheduler: {e}")
else:
    logger.warning("Slice scheduler not initialized (kill-switch or circuit-breaker unavailable)")

# Reconciliation service (startup gating + periodic sync)
reconciliation_service: ReconciliationService | None = None
reconciliation_task: asyncio.Task[None] | None = None

# ============================================================================
# FastAPI Application
# ============================================================================

app = FastAPI(
    title="Execution Gateway",
    description="Order execution service with idempotent submission and DRY_RUN mode",
    version=__version__,
    docs_url="/docs",
    redoc_url="/redoc",
)

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

# ============================================================================
# API Authentication Configuration (C6)
# ============================================================================
# Auth dependencies for trading endpoints. Defaults to enforce mode (fail-closed).
# Set API_AUTH_MODE=log_only for staged rollout.

order_submit_auth = api_auth(
    APIAuthConfig(
        action="order_submit",
        require_role=None,  # Role checked via permission
        require_permission=Permission.SUBMIT_ORDER,
    )
)

order_slice_auth = api_auth(
    APIAuthConfig(
        action="order_slice",
        require_role=None,
        require_permission=Permission.SUBMIT_ORDER,
    )
)

order_cancel_auth = api_auth(
    APIAuthConfig(
        action="order_cancel",
        require_role=None,
        require_permission=Permission.CANCEL_ORDER,
    )
)

order_read_auth = api_auth(
    APIAuthConfig(
        action="order_read",
        require_role=None,
        require_permission=Permission.VIEW_POSITIONS,
    )
)

kill_switch_auth = api_auth(
    APIAuthConfig(
        action="kill_switch",
        require_role=None,
        require_permission=Permission.CANCEL_ORDER,
    )
)

app.include_router(manual_controls_router, prefix="/api/v1", tags=["Manual Controls"])


# ============================================================================
# Auth Middleware
# ============================================================================
# Populate request.state.user from trusted internal headers.
# This middleware is required for RBAC-protected endpoints.
# In production, ensure only trusted internal services can reach this gateway.


class _SecretValue(Protocol):
    """Protocol for types that can provide a secret value (e.g., SecretStr)."""

    def get_secret_value(self) -> str: ...


class _InternalTokenSettings(Protocol):
    """Protocol for settings used in internal token validation.

    This allows both real Settings and test mocks to satisfy the type.
    """

    internal_token_required: bool
    internal_token_secret: _SecretValue
    internal_token_timestamp_tolerance_seconds: int


def _verify_internal_token(
    token: str | None,
    timestamp_str: str | None,
    user_id: str,
    role: str,
    strategies: str,
    settings: _InternalTokenSettings,
) -> tuple[bool, str]:
    """Verify X-User-Signature using HMAC-SHA256.

    Token format: HMAC-SHA256(secret, canonical_json_payload)
    where the payload is a JSON object with sorted keys:
    {"role": ..., "strats": ..., "ts": ..., "uid": ...}
    This prevents delimiter injection attacks that could occur with simple concatenation.

    Args:
        token: Value from X-User-Signature header
        timestamp_str: Value from X-Request-Timestamp header (epoch seconds)
        user_id: Value from X-User-Id header
        role: Value from X-User-Role header
        strategies: Value from X-User-Strategies header (comma-separated)
        settings: Application settings with internal_token_* config

    Returns:
        Tuple of (is_valid, error_reason). error_reason is empty if valid.

    Security Notes:
        - Uses constant-time comparison (hmac.compare_digest) to prevent timing attacks
        - Validates timestamp within ±tolerance_seconds to prevent replay attacks
        - Fails closed: missing token/timestamp when required returns False
        - Binds strategies to signature to prevent privilege escalation
    """
    if not settings.internal_token_required:
        return True, ""

    secret_value = settings.internal_token_secret.get_secret_value()
    if not secret_value:
        logger.error("INTERNAL_TOKEN_REQUIRED=true but INTERNAL_TOKEN_SECRET is empty")
        return False, "token_secret_not_configured"

    if not token:
        return False, "missing_token"

    if not timestamp_str:
        return False, "missing_timestamp"

    # Parse and validate timestamp
    try:
        request_timestamp = int(timestamp_str)
    except ValueError:
        return False, "invalid_timestamp_format"

    now = int(time.time())
    skew = abs(now - request_timestamp)
    if skew > settings.internal_token_timestamp_tolerance_seconds:
        logger.warning(
            "Internal token timestamp outside tolerance",
            extra={
                "skew_seconds": skew,
                "tolerance_seconds": settings.internal_token_timestamp_tolerance_seconds,
                "user_id_prefix": user_id[:4] if user_id else "none",
            },
        )
        return False, "timestamp_expired"

    # Compute expected signature using JSON payload to prevent delimiter injection
    # Example attack without JSON: user_id="u1:admin" + role="viewer"
    # could become user_id="u1" + role="admin:viewer"
    # JSON with sorted keys provides canonical representation immune to such attacks
    # Note: Replay protection is timestamp-based only. For stronger protection,
    # consider adding nonce validation with Redis in high-security environments.
    payload_data = {
        "uid": user_id.strip(),
        "role": role.strip(),
        "strats": strategies.strip(),
        "ts": timestamp_str.strip(),
    }
    payload = json.dumps(payload_data, separators=(",", ":"), sort_keys=True)
    expected_signature = hmac.new(
        secret_value.encode("utf-8"),
        payload.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()

    # Constant-time comparison to prevent timing attacks
    if not hmac.compare_digest(expected_signature, token.lower()):
        # Log mismatch without revealing signature prefixes to avoid leaking secret-derived data
        logger.warning(
            "Internal token signature mismatch",
            extra={
                "user_id_prefix": user_id[:4] if user_id else "none",
                "token_length": len(token) if token else 0,
            },
        )
        return False, "invalid_signature"

    return True, ""


@app.middleware("http")
async def populate_user_from_headers(request: Request, call_next: Any) -> Any:
    """Populate request.state.user from trusted internal headers.

    The performance dashboard Streamlit client sends X-User-Role, X-User-Id, and
    X-User-Strategies headers. This middleware validates these headers using
    HMAC-signed X-User-Signature when INTERNAL_TOKEN_REQUIRED=true.

    Headers:
        X-User-Role: User role (admin, trader, viewer)
        X-User-Id: User identifier
        X-User-Strategies: Comma-separated list of authorized strategies
        X-User-Signature: HMAC-SHA256 signature (when validation enabled)
        X-Request-Timestamp: Epoch seconds for replay protection

    Backward Compatibility:
        - INTERNAL_TOKEN_REQUIRED=false (explicit): Headers trusted without validation
        - INTERNAL_TOKEN_REQUIRED=true (default): Token validation required for user context

    This middleware populates request.state.user which _build_user_context
    then uses for RBAC enforcement.
    """
    role = request.headers.get("X-User-Role")
    user_id = request.headers.get("X-User-Id")
    strategies_header = request.headers.get("X-User-Strategies", "")

    if role and user_id:
        # Validate internal token if required
        settings = get_settings()
        if settings.internal_token_required:
            token = request.headers.get("X-User-Signature")
            timestamp = request.headers.get("X-Request-Timestamp")

            is_valid, error_reason = _verify_internal_token(
                token=token,
                timestamp_str=timestamp,
                user_id=user_id,
                role=role,
                strategies=strategies_header,
                settings=cast(_InternalTokenSettings, settings),
            )

            if not is_valid:
                logger.warning(
                    "Internal token validation failed",
                    extra={
                        "error_reason": error_reason,
                        "path": request.url.path,
                        "user_id_prefix": user_id[:4] if user_id else "none",
                    },
                )
                return JSONResponse(
                    status_code=401,
                    content={"detail": "Invalid or missing internal authentication token"},
                )

        strategies = [s.strip() for s in strategies_header.split(",") if s.strip()]
        request.state.user = {
            "role": role.strip(),
            "user_id": user_id.strip(),
            "strategies": strategies,
        }

    return await call_next(request)


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


def _handle_idempotency_race(
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


def _parse_webhook_timestamp(*timestamps: Any, default: datetime) -> datetime:
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


async def _resolve_fat_finger_context(
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
            realtime_prices = _batch_fetch_realtime_prices_from_redis([order.symbol], redis_client)
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


def _fat_finger_thresholds_snapshot() -> FatFingerThresholdsResponse:
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
    except Exception as exc:
        logger.error(
            "Quarantine check failed",
            extra={"symbol": symbol, "error": str(exc)},
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "error": "Quarantine check unavailable",
                "message": "Redis unavailable for quarantine enforcement (fail-closed).",
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
    except Exception as exc:
        logger.error(
            "Failed to acquire reduce-only lock",
            extra={"symbol": order.symbol, "error": str(exc)},
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "error": "Reduce-only lock unavailable",
                "message": "Unable to acquire reduce-only validation lock (fail-closed).",
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
            except Exception as exc:
                logger.warning(
                    "Failed to release reduce-only lock",
                    extra={"symbol": order.symbol, "error": str(exc)},
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
# Helper Functions
# ============================================================================


def _batch_fetch_realtime_prices_from_redis(
    symbols: list[str], redis_client: RedisClient | None
) -> dict[str, tuple[Decimal | None, datetime | None]]:
    """
    Batch fetch real-time prices from Redis for multiple symbols.

    This function solves the N+1 query problem by fetching all prices in a single
    MGET call instead of individual GET calls for each symbol.

    Args:
        symbols: List of stock symbols to fetch
        redis_client: Redis client instance

    Returns:
        Dictionary mapping symbol to (price, timestamp) tuple.
        Missing symbols will have (None, None) as value.

    Performance:
        - 1 Redis call vs N calls (where N = number of symbols)
        - 5-10x faster for 10+ symbols
        - Reduces network round-trips from O(N) to O(1)

    Notes:
        - Returns empty dict if Redis unavailable
        - Returns (None, None) for symbols not in cache
        - Handles parsing errors gracefully per symbol
    """
    if not redis_client or not symbols:
        return dict.fromkeys(symbols, (None, None))

    try:
        # Build Redis keys for batch fetch
        price_keys = [RedisKeys.price(symbol) for symbol in symbols]

        # Batch fetch all prices in one Redis call (O(1) network round-trip)
        price_values = redis_client.mget(price_keys)

        # Initialize results with default (None, None) for all symbols (DRY principle)
        result: dict[str, tuple[Decimal | None, datetime | None]] = dict.fromkeys(
            symbols, (None, None)
        )

        # Parse results and update dictionary for symbols with valid data
        for symbol, price_json in zip(symbols, price_values, strict=False):
            if not price_json:
                continue  # Skip symbols not found in cache (already (None, None))

            try:
                price_data = json.loads(price_json)
                price = Decimal(str(price_data["mid"]))
                timestamp = datetime.fromisoformat(price_data["timestamp"])
                result[symbol] = (price, timestamp)
                logger.debug(f"Batch fetched price for {symbol}: ${price}")
            except (json.JSONDecodeError, KeyError, ValueError, TypeError, InvalidOperation) as e:
                # Log error but no need to set result[symbol] - already (None, None)
                logger.warning(f"Failed to parse price for {symbol} from batch fetch: {e}")

        return result

    except RedisError as e:
        # Catch all Redis errors (connection, timeout, etc.) for graceful degradation
        logger.warning(f"Failed to batch fetch prices for {len(symbols)} symbols: {e}")
        return dict.fromkeys(symbols, (None, None))


def _calculate_position_pnl(
    pos: Position,
    current_price: Decimal,
    price_source: Literal["real-time", "database", "fallback"],
    last_price_update: datetime | None,
) -> RealtimePositionPnL:
    """
    Calculate unrealized P&L for a single position.

    Args:
        pos: Position from database
        current_price: Current market price
        price_source: Source of current price (real-time/database/fallback)
        last_price_update: Timestamp of last price update (if available)

    Returns:
        RealtimePositionPnL with calculated P&L values
    """
    # Calculate unrealized P&L
    unrealized_pl = (current_price - pos.avg_entry_price) * pos.qty

    # Calculate P&L percentage based on actual profit/loss
    # This works correctly for both long and short positions
    unrealized_pl_pct = (
        (unrealized_pl / (pos.avg_entry_price * abs(pos.qty))) * Decimal("100")
        if pos.avg_entry_price > 0 and pos.qty != 0
        else Decimal("0")
    )

    return RealtimePositionPnL(
        symbol=pos.symbol,
        qty=pos.qty,
        avg_entry_price=pos.avg_entry_price,
        current_price=current_price,
        price_source=price_source,
        unrealized_pl=unrealized_pl,
        unrealized_pl_pct=unrealized_pl_pct,
        last_price_update=last_price_update,
    )


def _resolve_and_calculate_pnl(
    pos: Position,
    realtime_price_data: tuple[Decimal | None, datetime | None],
) -> tuple[RealtimePositionPnL, bool]:
    """
    Resolve price from multiple sources and calculate P&L for a position.

    Implements three-tier price fallback:
    1. Real-time price from Redis (Market Data Service)
    2. Database price (last known price)
    3. Entry price (ultimate fallback)

    Args:
        pos: Position from database
        realtime_price_data: Tuple of (price, timestamp) from batch Redis fetch

    Returns:
        Tuple of (position P&L, is_realtime flag)

    Notes:
        - Extracted from get_realtime_pnl for improved modularity
        - Makes main endpoint loop more concise and readable
        - Replaces deprecated single-symbol _fetch_realtime_price_from_redis

    See Also:
        - Gemini review: apps/execution_gateway/main.py MEDIUM priority refactoring
    """
    realtime_price, last_price_update = realtime_price_data

    # Three-tier price fallback
    current_price: Decimal
    price_source: Literal["real-time", "database", "fallback"]
    is_realtime: bool

    if realtime_price is not None:
        current_price, price_source, is_realtime = realtime_price, "real-time", True
    elif pos.current_price is not None:
        current_price, price_source, is_realtime = pos.current_price, "database", False
        last_price_update = None
    else:
        current_price, price_source, is_realtime = pos.avg_entry_price, "fallback", False
        last_price_update = None

    # Calculate P&L with resolved price
    position_pnl = _calculate_position_pnl(pos, current_price, price_source, last_price_update)

    return position_pnl, is_realtime


# ============================================================================
# Performance Dashboard Helpers
# ============================================================================


def _performance_cache_key(
    start_date: date, end_date: date, strategies: tuple[str, ...], user_id: str | None
) -> str:
    """Create cache key for performance range scoped by strategies and user.

    Per T6.2 plan iteration 10, the cache must be user-scoped AND strategy-scoped
    to prevent cross-user leakage and stale data when RBAC assignments change.
    """

    strat_token = "none" if not strategies else ",".join(sorted(strategies))
    strat_hash = hashlib.md5(strat_token.encode()).hexdigest()[:8]
    user_token = user_id or "anon"
    return f"performance:daily:{user_token}:{start_date}:{end_date}:{strat_hash}"


def _build_user_context(request: Request) -> dict[str, Any]:
    """
    Extract user context for RBAC.

    Fail closed when no authenticated user is attached to the request. Upstream
    middleware must populate ``request.state.user`` with a trusted object or
    mapping that includes a role (and optionally strategies and user_id).
    Client-provided headers are intentionally ignored to avoid spoofing.
    """

    state_user = getattr(request.state, "user", None)

    if state_user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing authenticated user context",
        )

    if isinstance(state_user, dict):
        user: dict[str, Any] = dict(state_user)
    else:
        user = dict(getattr(state_user, "__dict__", {}))
        # Preserve common attributes even if __dict__ is empty/filtered
        for attr in ("role", "strategies", "user_id", "id"):
            value = getattr(state_user, attr, None)
            if value is not None:
                user.setdefault("user_id" if attr == "id" else attr, value)

    if not user.get("role"):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing authenticated user context",
        )

    requested_strategies = request.query_params.getlist("strategies")

    return {
        "role": user.get("role"),
        "strategies": user.get("strategies", []),
        "requested_strategies": requested_strategies,
        "user_id": user.get("user_id"),
        "user": user,
    }


def _performance_cache_index_key(trade_date: date) -> str:
    """Key used to track which cache entries include a given trade date."""

    return f"performance:daily:index:{trade_date}"


def _register_performance_cache(cache_key: str, start_date: date, end_date: date) -> None:
    """Track cached ranges by each included trade date for targeted invalidation."""

    if not redis_client:
        return

    try:
        pipe = redis_client.pipeline()
        current = start_date
        while current <= end_date:
            index_key = _performance_cache_index_key(current)
            pipe.sadd(index_key, cache_key)
            pipe.expire(index_key, PERFORMANCE_CACHE_TTL)
            current += timedelta(days=1)
        pipe.execute()
    except Exception as e:
        logger.warning(f"Performance cache index registration failed: {e}")


def _invalidate_performance_cache(trade_date: date | None = None) -> None:
    """Invalidate cached performance ranges that include the given trade_date.

    Falls back to today's date when trade_date is not provided. This avoids a
    global SCAN across all cache keys by leveraging per-date index sets that are
    maintained when caching responses.

    Uses SSCAN instead of SMEMBERS to avoid blocking the Redis event loop for
    large sets. Cache keys and index key are deleted atomically in a single
    call to prevent stale index entries if the process fails mid-operation.
    """

    if not redis_client:
        return

    target_date = trade_date or date.today()
    index_key = _performance_cache_index_key(target_date)

    try:
        # Stream deletions in batches to maintain O(1) memory for large index sets
        batch: list[str] = []
        batch_size = 100

        for key in redis_client.sscan_iter(index_key):
            batch.append(key)
            if len(batch) >= batch_size:
                redis_client.delete(*batch)
                batch = []

        # Delete remaining batch + index key
        if batch:
            redis_client.delete(*batch, index_key)
        else:
            # If batch is empty, just delete the index key.
            # This covers cases where the set was empty or its size was a multiple of batch_size.
            redis_client.delete(index_key)
    except Exception as e:
        logger.warning(f"Performance cache invalidation failed: {e}")


def _compute_daily_performance(
    rows: list[dict[str, Any]], start_date: date, end_date: date
) -> tuple[list[DailyPnL], Decimal, Decimal]:
    """Build filled daily series with cumulative and drawdown.

    Drawdown is measured versus the running peak of cumulative P&L. The peak is
    initialized to the first cumulative value to correctly capture sequences
    that begin with losses (all-negative series).
    """

    if not rows:
        return [], Decimal("0"), Decimal("0")

    # DESIGN DECISION: Expand requested range to cover returned data.
    # This supports mocked data in tests where mock databases may return dates outside
    # the requested range. Keeping this in production code ensures test/prod parity
    # and gracefully handles edge cases where fill timestamps cross date boundaries.
    # Alternative: Move to test helper, but risks test/prod divergence.
    trade_dates: list[date] = [
        t for t in (r.get("trade_date") for r in rows) if isinstance(t, date)
    ]
    if trade_dates:
        earliest = min(trade_dates)
        latest = max(trade_dates)
        if earliest < start_date:
            start_date = earliest
        if latest > end_date:
            end_date = latest

    rows_by_date: dict[date, dict[str, Decimal | int]] = {}
    for r in rows:
        trade_date_raw = r.get("trade_date")
        if not isinstance(trade_date_raw, date):
            continue
        rows_by_date[trade_date_raw] = {
            "realized_pl": Decimal(str(r.get("daily_realized_pl", 0))),
            "closing_trade_count": int(r.get("closing_trade_count") or 0),
        }

    daily: list[DailyPnL] = []
    cumulative = Decimal("0")
    peak: Decimal | None = None  # first cumulative value will seed peak
    max_drawdown = Decimal("0")

    # Skip leading days with no data so peak is seeded by first real trade day
    first_trade_date = min(rows_by_date.keys()) if rows_by_date else start_date
    current = max(start_date, first_trade_date)
    one_day = timedelta(days=1)

    while current <= end_date:
        day_data = rows_by_date.get(
            current, {"realized_pl": Decimal("0"), "closing_trade_count": 0}
        )
        realized = cast(Decimal, day_data["realized_pl"])
        closing_count = int(day_data["closing_trade_count"])

        cumulative += realized
        if peak is None:
            peak = cumulative
        if cumulative > peak:
            peak = cumulative

        # Use absolute peak to handle negative starting equity; avoid divide by zero
        if peak != 0:
            drawdown_pct = (cumulative - peak) / abs(peak) * Decimal("100")
        else:
            drawdown_pct = Decimal("0")

        if drawdown_pct < max_drawdown:
            max_drawdown = drawdown_pct

        daily.append(
            DailyPnL(
                date=current,
                realized_pl=realized,
                cumulative_realized_pl=cumulative,
                peak_equity=peak,
                drawdown_pct=drawdown_pct,
                closing_trade_count=closing_count,
            )
        )

        current += one_day

    return daily, cumulative, max_drawdown


# ============================================================================
# Endpoints
# ============================================================================


@app.get("/", tags=["Health"])
async def root() -> dict[str, Any]:
    """Root endpoint."""
    return {
        "service": "execution_gateway",
        "version": __version__,
        "status": "running",
        "dry_run": DRY_RUN,
    }


@app.get("/health", response_model=HealthResponse, tags=["Health"])
async def health_check() -> HealthResponse:
    """
    Health check endpoint.

    Returns service health status including:
    - Overall status (healthy, degraded, unhealthy)
    - Database connection status
    - Alpaca connection status (if not DRY_RUN)
    - Service version and configuration

    Returns:
        HealthResponse with service health details

    Examples:
        >>> import requests
        >>> response = requests.get("http://localhost:8002/health")
        >>> response.json()
        {
            "status": "healthy",
            "service": "execution_gateway",
            "version": "0.1.0",
            "dry_run": true,
            "database_connected": true,
            "alpaca_connected": true,
            "timestamp": "2024-10-17T16:30:00Z"
        }
    """
    # Check database connection
    db_connected = db_client.check_connection()

    # Check Redis connection and attempt infrastructure recovery via RecoveryManager
    redis_connected = False
    if redis_client:
        redis_connected = redis_client.health_check()

        if redis_connected:
            # Factories only called when components verified available by RecoveryManager
            recovery_manager.attempt_recovery(
                kill_switch_factory=lambda: KillSwitch(redis_client=redis_client),
                circuit_breaker_factory=lambda: CircuitBreaker(redis_client=redis_client),
                position_reservation_factory=lambda: PositionReservation(redis=redis_client),
                slice_scheduler_factory=lambda: SliceScheduler(
                    kill_switch=recovery_manager.kill_switch,  # type: ignore[arg-type]
                    breaker=recovery_manager.circuit_breaker,  # type: ignore[arg-type]
                    db_client=db_client,
                    executor=alpaca_client,  # Can be None in DRY_RUN mode
                ),
            )

    # Check Alpaca connection (if not DRY_RUN)
    alpaca_connected = True
    if not DRY_RUN and alpaca_client:
        alpaca_api_status = "success"
        try:
            alpaca_connected = alpaca_client.check_connection()
        except Exception:
            alpaca_api_status = "error"
            alpaca_connected = False
        finally:
            # Always track Alpaca API request metric
            alpaca_api_requests_total.labels(
                operation="check_connection", status=alpaca_api_status
            ).inc()

    # Update health metrics
    database_connection_status.set(1 if db_connected else 0)
    redis_connection_status.set(1 if redis_connected else 0)
    alpaca_connection_status.set(1 if (not DRY_RUN and alpaca_connected) else 0)

    # Determine overall status
    overall_status: Literal["healthy", "degraded", "unhealthy"]
    if recovery_manager.needs_recovery():
        # Safety mechanisms unavailable means we're in fail-closed mode - report degraded
        overall_status = "degraded"
    elif db_connected and (DRY_RUN or alpaca_connected):
        overall_status = "healthy"
    elif db_connected:
        overall_status = "degraded"  # DB OK but Alpaca down
    else:
        overall_status = "unhealthy"  # DB down

    return HealthResponse(
        status=overall_status,
        service="execution_gateway",
        version=__version__,
        dry_run=DRY_RUN,
        database_connected=db_connected,
        alpaca_connected=alpaca_connected,
        timestamp=datetime.now(UTC),
        details={
            "strategy_id": STRATEGY_ID,
            "alpaca_base_url": ALPACA_BASE_URL if not DRY_RUN else None,
        },
    )


@app.get("/api/v1/config", response_model=ConfigResponse, tags=["Configuration"])
async def get_config() -> ConfigResponse:
    """
    Get service configuration for verification.

    Returns safety flags and environment settings for automated verification
    in smoke tests and monitoring. Critical for ensuring paper trading mode
    in staging and detecting configuration drift.

    This endpoint is used by:
    - CI/CD smoke tests to verify paper trading mode active
    - Monitoring systems to detect configuration drift
    - Debugging to verify environment settings

    Returns:
        ConfigResponse with service configuration details

    Examples:
        >>> import requests
        >>> response = requests.get("http://localhost:8002/api/v1/config")
        >>> config = response.json()
        >>> assert config["dry_run"] is True  # Staging safety check
        >>> assert config["alpaca_paper"] is True
        >>> assert config["environment"] == "staging"
    """
    return ConfigResponse(
        service="execution_gateway",
        version=__version__,
        environment=ENVIRONMENT,
        dry_run=DRY_RUN,
        alpaca_paper=ALPACA_PAPER,
        circuit_breaker_enabled=CIRCUIT_BREAKER_ENABLED,
        liquidity_check_enabled=LIQUIDITY_CHECK_ENABLED,
        max_slice_pct_of_adv=MAX_SLICE_PCT_OF_ADV,
        timestamp=datetime.now(UTC),
    )


@app.get(
    "/api/v1/fat-finger/thresholds",
    response_model=FatFingerThresholdsResponse,
    tags=["Configuration"],
)
async def get_fat_finger_thresholds() -> FatFingerThresholdsResponse:
    """Get current fat-finger threshold configuration."""

    return _fat_finger_thresholds_snapshot()


@app.put(
    "/api/v1/fat-finger/thresholds",
    response_model=FatFingerThresholdsResponse,
    tags=["Configuration"],
)
@require_permission(Permission.MANAGE_STRATEGIES)
async def update_fat_finger_thresholds(
    payload: FatFingerThresholdsUpdateRequest,
    user: dict[str, Any] = Depends(_build_user_context),
) -> FatFingerThresholdsResponse:
    """Update fat-finger thresholds (defaults and per-symbol overrides)."""

    if payload.default_thresholds is not None:
        fat_finger_validator.update_defaults(payload.default_thresholds)

    if payload.symbol_overrides is not None:
        fat_finger_validator.update_symbol_overrides(payload.symbol_overrides)

    logger.info(
        "Fat-finger thresholds updated",
        extra={
            "user_id": user.get("user_id"),
            "default_thresholds": (
                payload.default_thresholds.model_dump(mode="json")
                if payload.default_thresholds
                else None
            ),
            "symbol_overrides": (
                list(payload.symbol_overrides.keys()) if payload.symbol_overrides else []
            ),
        },
    )

    return _fat_finger_thresholds_snapshot()


# =============================================================================
# Strategy Status Endpoints
# =============================================================================


def _determine_strategy_status(
    db_status: dict[str, Any], now: datetime
) -> Literal["active", "paused", "error", "inactive"]:
    """Determine strategy status based on activity.

    A strategy is considered active if it has:
    - Open positions (positions_count > 0)
    - Open orders (open_orders_count > 0)
    - Recent signal activity (within 24 hours)

    Args:
        db_status: Dict with positions_count, open_orders_count, last_signal_at
        now: Current timestamp for age calculation

    Returns:
        Strategy status: "active", "paused", "error", or "inactive"
    """
    if db_status["positions_count"] > 0 or db_status["open_orders_count"] > 0:
        return "active"
    if db_status["last_signal_at"]:
        age = (now - db_status["last_signal_at"]).total_seconds()
        if age < STRATEGY_ACTIVITY_THRESHOLD_SECONDS:
            return "active"
    return "inactive"


@app.get(
    "/api/v1/strategies",
    response_model=StrategiesListResponse,
    tags=["Strategies"],
)
async def list_strategies(
    user: dict[str, Any] = Depends(_build_user_context),
) -> StrategiesListResponse:
    """
    List all strategies with their current status.

    Returns consolidated view of each strategy including:
    - Basic info (id, name, status)
    - Position and open order counts
    - Today's realized P&L
    - Last signal time

    Only returns strategies the user is authorized to view.

    Example response:
        {
            "strategies": [
                {
                    "strategy_id": "alpha_baseline",
                    "name": "Alpha Baseline",
                    "status": "active",
                    ...
                }
            ],
            "total_count": 1,
            "timestamp": "2024-10-17T16:35:00Z"
        }
    """
    now = datetime.now(UTC)

    # Get authorized strategies for this user
    authorized_strategies = get_authorized_strategies(user.get("user"))
    if not authorized_strategies:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="No strategy access",
        )

    # Get strategy IDs filtered at database level for better performance
    # Uses ANY(...) to avoid transferring all strategy IDs then filtering in Python
    strategy_ids = db_client.get_all_strategy_ids(filter_ids=authorized_strategies)

    # Fetch all strategy statuses in a single bulk query (avoids N+1 problem)
    bulk_status = db_client.get_bulk_strategy_status(strategy_ids)

    strategies = []
    for strategy_id in strategy_ids:
        db_status = bulk_status.get(strategy_id)
        if db_status is None:
            continue

        strategy_status = _determine_strategy_status(db_status, now)

        strategies.append(
            StrategyStatusResponse(
                strategy_id=strategy_id,
                name=strategy_id.replace("_", " ").title(),  # Simple name formatting
                status=strategy_status,
                model_version=None,  # Could be fetched from model registry
                model_status=None,
                last_signal_at=db_status["last_signal_at"],
                last_error=None,
                positions_count=db_status["positions_count"],
                open_orders_count=db_status["open_orders_count"],
                today_pnl=db_status["today_pnl"],
                timestamp=now,
            )
        )

    return StrategiesListResponse(
        strategies=strategies,
        total_count=len(strategies),
        timestamp=now,
    )


@app.get(
    "/api/v1/strategies/{strategy_id}",
    response_model=StrategyStatusResponse,
    tags=["Strategies"],
)
async def get_strategy_status(
    strategy_id: str,
    user: dict[str, Any] = Depends(_build_user_context),
) -> StrategyStatusResponse:
    """
    Get status for a specific strategy.

    Args:
        strategy_id: The strategy identifier

    Returns:
        StrategyStatusResponse with consolidated strategy state

    Raises:
        HTTPException 403 if user not authorized for this strategy
        HTTPException 404 if strategy not found
    """
    now = datetime.now(UTC)

    # Check if user is authorized for this strategy
    authorized_strategies = get_authorized_strategies(user.get("user"))
    if not authorized_strategies:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="No strategy access",
        )
    if strategy_id not in authorized_strategies:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Not authorized to access strategy '{strategy_id}'",
        )

    db_status = db_client.get_strategy_status(strategy_id)
    if db_status is None:
        raise HTTPException(
            status_code=404,
            detail=f"Strategy '{strategy_id}' not found",
        )

    strategy_status = _determine_strategy_status(db_status, now)

    return StrategyStatusResponse(
        strategy_id=strategy_id,
        name=strategy_id.replace("_", " ").title(),
        status=strategy_status,
        model_version=None,
        model_status=None,
        last_signal_at=db_status["last_signal_at"],
        last_error=None,
        positions_count=db_status["positions_count"],
        open_orders_count=db_status["open_orders_count"],
        today_pnl=db_status["today_pnl"],
        timestamp=now,
    )


@app.post("/api/v1/kill-switch/engage", tags=["Kill-Switch"])
async def engage_kill_switch(
    request: KillSwitchEngageRequest,
    _auth_context: AuthContext = Depends(kill_switch_auth),
) -> dict[str, Any]:
    """
    Engage kill-switch (emergency trading halt).

    CRITICAL: This operator-controlled action immediately blocks ALL trading
    activities across all services until manually disengaged.

    Args:
        request: KillSwitchEngageRequest with reason, operator, and optional details

    Returns:
        Kill-switch status after engagement

    Raises:
        HTTPException 503: Redis unavailable
        HTTPException 400: Kill-switch already engaged

    Examples:
        >>> import requests
        >>> response = requests.post(
        ...     "http://localhost:8002/api/v1/kill-switch/engage",
        ...     json={
        ...         "reason": "Market anomaly detected",
        ...         "operator": "ops_team",
        ...         "details": {"anomaly_type": "flash_crash"}
        ...     }
        ... )
        >>> response.json()
        {"state": "ENGAGED", "engaged_by": "ops_team", ...}
    """
    kill_switch = recovery_manager.kill_switch
    if not kill_switch or recovery_manager.is_kill_switch_unavailable():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Kill-switch unavailable (Redis not initialized)",
        )

    try:
        kill_switch.engage(
            reason=request.reason, operator=request.operator, details=request.details
        )
        return kill_switch.get_status()
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        ) from e
    except RuntimeError as e:
        # Kill-switch state missing (fail-closed)
        recovery_manager.set_kill_switch_unavailable(True)
        logger.error(
            "Kill-switch engage failed: state missing (fail-closed)",
            extra={"fail_closed": True, "error": str(e)},
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "error": "Kill-switch unavailable",
                "message": "Kill-switch state missing in Redis (fail-closed for safety)",
                "fail_closed": True,
            },
        ) from e


@app.post("/api/v1/kill-switch/disengage", tags=["Kill-Switch"])
async def disengage_kill_switch(
    request: KillSwitchDisengageRequest,
    _auth_context: AuthContext = Depends(kill_switch_auth),
) -> dict[str, Any]:
    """
    Disengage kill-switch (resume trading).

    This operator action re-enables trading after kill-switch was engaged.

    Args:
        request: KillSwitchDisengageRequest with operator and optional notes

    Returns:
        Kill-switch status after disengagement

    Raises:
        HTTPException 503: Redis unavailable
        HTTPException 400: Kill-switch not currently engaged

    Examples:
        >>> import requests
        >>> response = requests.post(
        ...     "http://localhost:8002/api/v1/kill-switch/disengage",
        ...     json={
        ...         "operator": "ops_team",
        ...         "notes": "Market conditions normalized"
        ...     }
        ... )
        >>> response.json()
        {"state": "ACTIVE", "disengaged_by": "ops_team", ...}
    """
    kill_switch = recovery_manager.kill_switch
    if not kill_switch or recovery_manager.is_kill_switch_unavailable():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Kill-switch unavailable (Redis not initialized)",
        )

    try:
        kill_switch.disengage(operator=request.operator, notes=request.notes)
        return kill_switch.get_status()
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        ) from e
    except RuntimeError as e:
        # Kill-switch state missing (fail-closed)
        recovery_manager.set_kill_switch_unavailable(True)
        logger.error(
            "Kill-switch disengage failed: state missing (fail-closed)",
            extra={"fail_closed": True, "error": str(e)},
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "error": "Kill-switch unavailable",
                "message": "Kill-switch state missing in Redis (fail-closed for safety)",
                "fail_closed": True,
            },
        ) from e


@app.get("/api/v1/kill-switch/status", tags=["Kill-Switch"])
async def get_kill_switch_status(
    _auth_context: AuthContext = Depends(kill_switch_auth),
) -> dict[str, Any]:
    """
    Get kill-switch status.

    Returns current state, last engagement/disengagement details, and history.

    Returns:
        Kill-switch status with state, timestamps, and operator info

    Raises:
        HTTPException 503: Redis unavailable

    Examples:
        >>> import requests
        >>> response = requests.get("http://localhost:8002/api/v1/kill-switch/status")
        >>> status = response.json()
        >>> print(status["state"])
        'ACTIVE'
    """
    kill_switch = recovery_manager.kill_switch
    if not kill_switch or recovery_manager.is_kill_switch_unavailable():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Kill-switch unavailable (Redis not initialized)",
        )

    try:
        return kill_switch.get_status()
    except RuntimeError as e:
        # Kill-switch state missing (fail-closed)
        recovery_manager.set_kill_switch_unavailable(True)
        logger.error(
            "Kill-switch status unavailable: state missing (fail-closed)",
            extra={"fail_closed": True, "error": str(e)},
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "error": "Kill-switch unavailable",
                "message": "Kill-switch state missing in Redis (fail-closed for safety)",
                "fail_closed": True,
            },
        ) from e


@app.get("/api/v1/reconciliation/status", tags=["Reconciliation"])
async def get_reconciliation_status() -> dict[str, Any]:
    """Return reconciliation gating status and override state."""
    if DRY_RUN:
        return {
            "startup_complete": True,
            "dry_run": True,
            "message": "DRY_RUN mode - reconciliation gating disabled",
        }

    if not reconciliation_service:
        return {
            "startup_complete": False,
            "dry_run": False,
            "message": "Reconciliation service not initialized",
        }

    return {
        "startup_complete": reconciliation_service.is_startup_complete(),
        "dry_run": DRY_RUN,
        "startup_elapsed_seconds": reconciliation_service.startup_elapsed_seconds(),
        "startup_timed_out": reconciliation_service.startup_timed_out(),
        "override_active": reconciliation_service.override_active(),
        "override_context": reconciliation_service.override_context(),
    }


@app.post("/api/v1/reconciliation/run", tags=["Reconciliation"])
@require_permission(Permission.MANAGE_RECONCILIATION)
async def run_reconciliation(
    user: dict[str, Any] = Depends(_build_user_context),
) -> dict[str, Any]:
    """Manually trigger reconciliation."""
    if DRY_RUN:
        return {"status": "skipped", "message": "DRY_RUN mode - reconciliation disabled"}
    if not reconciliation_service:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Reconciliation service not initialized",
        )

    await reconciliation_service.run_reconciliation_once("manual")
    return {"status": "ok", "message": "Reconciliation run complete"}


@app.post("/api/v1/reconciliation/force-complete", tags=["Reconciliation"])
@require_permission(Permission.MANAGE_RECONCILIATION)
async def force_complete_reconciliation(
    payload: ReconciliationForceCompleteRequest,
    user: dict[str, Any] = Depends(_build_user_context),
) -> dict[str, Any]:
    """Force-complete reconciliation (operator override)."""
    if DRY_RUN:
        return {"status": "skipped", "message": "DRY_RUN mode - reconciliation disabled"}
    if not reconciliation_service:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Reconciliation service not initialized",
        )

    user_id = user.get("user_id") if isinstance(user, dict) else None

    reconciliation_service.mark_startup_complete(
        forced=True, user_id=user_id, reason=payload.reason
    )
    logger.warning(
        "Reconciliation force-complete invoked",
        extra={"user_id": user_id, "reason": payload.reason},
    )
    return {
        "status": "override_enabled",
        "message": "Reconciliation marked complete by operator override",
        "user_id": user_id,
        "reason": payload.reason,
    }


@app.post("/api/v1/orders", response_model=OrderResponse, tags=["Orders"])
async def submit_order(
    order: OrderRequest,
    response: Response,
    # IMPORTANT: Auth must run BEFORE rate limiting to populate request.state with user context
    # This allows rate limiter to bucket by user/service instead of anonymous IP
    _auth_context: AuthContext = Depends(order_submit_auth),
    _rate_limit_remaining: int = Depends(order_submit_rl),
) -> OrderResponse:
    """
    Submit order with idempotent retry semantics.

    The order is assigned a deterministic client_order_id based on the order
    parameters and current date. This ensures that the same order submitted
    multiple times will have the same ID and won't create duplicates.

    In DRY_RUN mode (default), orders are logged to database but NOT submitted
    to Alpaca. Set DRY_RUN=false to enable actual paper trading.

    Args:
        order: Order request (symbol, side, qty, order_type, etc.)

    Returns:
        OrderResponse with client_order_id, status, and broker_order_id

    Raises:
        HTTPException 400: Invalid order parameters
        HTTPException 422: Order rejected by broker
        HTTPException 503: Broker connection error

    Examples:
        Market buy order:
        >>> import requests
        >>> response = requests.post(
        ...     "http://localhost:8002/api/v1/orders",
        ...     json={
        ...         "symbol": "AAPL",
        ...         "side": "buy",
        ...         "qty": 10,
        ...         "order_type": "market"
        ...     }
        ... )
        >>> response.json()
        {
            "client_order_id": "a1b2c3d4e5f6...",
            "status": "dry_run",  # or "pending_new" if DRY_RUN=false
            "broker_order_id": null,
            "symbol": "AAPL",
            "side": "buy",
            "qty": 10,
            "order_type": "market",
            "limit_price": null,
            "created_at": "2024-10-17T16:30:00Z",
            "message": "Order logged (DRY_RUN mode)"
        }

        Limit sell order:
        >>> response = requests.post(
        ...     "http://localhost:8002/api/v1/orders",
        ...     json={
        ...         "symbol": "MSFT",
        ...         "side": "sell",
        ...         "qty": 5,
        ...         "order_type": "limit",
        ...         "limit_price": "300.50"
        ...     }
        ... )
    """
    # Safety gating uses RecoveryManager (thread-safe, fail-closed)
    # Start timing for metrics
    start_time = time.time()

    # Generate deterministic client_order_id
    client_order_id = generate_client_order_id(order, STRATEGY_ID)

    logger.info(
        f"Order request received: {order.symbol} {order.side} {order.qty}",
        extra={
            "client_order_id": client_order_id,
            "symbol": order.symbol,
            "side": order.side,
            "qty": order.qty,
            "order_type": order.order_type,
        },
    )

    kill_switch = recovery_manager.kill_switch
    circuit_breaker = recovery_manager.circuit_breaker
    position_reservation = recovery_manager.position_reservation

    # Check kill-switch unavailable (fail closed for safety)
    if recovery_manager.is_kill_switch_unavailable() or kill_switch is None:
        logger.error(
            f"🔴 Order blocked by unavailable kill-switch (FAIL CLOSED): {client_order_id}",
            extra={
                "client_order_id": client_order_id,
                "kill_switch_unavailable": True,
            },
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "error": "Kill-switch unavailable",
                "message": "All trading blocked - kill-switch state unknown (Redis unavailable)",
                "fail_closed": True,
            },
        )

    # Codex CRITICAL fix: Check circuit breaker unavailable (fail closed for safety)
    # This prevents trading when circuit breaker init failed or Redis unavailable
    if recovery_manager.is_circuit_breaker_unavailable() or circuit_breaker is None:
        logger.error(
            f"🔴 Order blocked by unavailable circuit breaker (FAIL CLOSED): {client_order_id}",
            extra={
                "client_order_id": client_order_id,
                "circuit_breaker_unavailable": True,
            },
        )
        _record_order_metrics(order, start_time, "blocked")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "error": "Circuit breaker unavailable",
                "message": (
                    "All trading blocked - circuit breaker state unknown " "(initialization failed)"
                ),
                "fail_closed": True,
            },
        )

    # Gemini PR fix: Check position reservation unavailable (fail closed for safety)
    # This prevents trading when position reservation init failed or Redis unavailable
    pos_res_unavailable = (
        recovery_manager.is_position_reservation_unavailable() or position_reservation is None
    )
    if pos_res_unavailable:
        logger.error(
            "🔴 Order blocked by unavailable position reservation "
            f"(FAIL CLOSED): {client_order_id}",
            extra={
                "client_order_id": client_order_id,
                "position_reservation_unavailable": True,
            },
        )
        _record_order_metrics(order, start_time, "blocked")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "error": "Position reservation unavailable",
                "message": (
                    "All trading blocked - position reservation state unknown "
                    "(initialization failed)"
                ),
                "fail_closed": True,
            },
        )

    # Check kill-switch (operator-controlled emergency halt)
    try:
        if kill_switch and kill_switch.is_engaged():
            status_info = kill_switch.get_status()
            logger.error(
                f"🔴 Order blocked by KILL-SWITCH: {client_order_id}",
                extra={
                    "client_order_id": client_order_id,
                    "kill_switch_engaged": True,
                    "engaged_by": status_info.get("engaged_by"),
                    "engagement_reason": status_info.get("engagement_reason"),
                },
            )
            # Gemini suggestion: Record metrics for kill-switch blocked orders
            _record_order_metrics(order, start_time, "blocked")
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={
                    "error": "Kill-switch engaged",
                    "message": "All trading halted by operator",
                    "engaged_by": status_info.get("engaged_by"),
                    "reason": status_info.get("engagement_reason"),
                    "engaged_at": status_info.get("engaged_at"),
                },
            )
    except RuntimeError as e:
        # Kill-switch state missing (fail-closed)
        recovery_manager.set_kill_switch_unavailable(True)
        logger.error(
            f"🔴 Order blocked by unavailable kill-switch (FAIL CLOSED): {client_order_id}",
            extra={
                "client_order_id": client_order_id,
                "kill_switch_unavailable": True,
                "fail_closed": True,
                "error": str(e),
            },
        )
        # Gemini suggestion: Record metrics for kill-switch blocked orders
        _record_order_metrics(order, start_time, "blocked")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "error": "Kill-switch unavailable",
                "message": "Kill-switch state missing in Redis (fail-closed for safety)",
                "fail_closed": True,
            },
        ) from e

    # C7 Fix: Check circuit breaker (automatic risk-based halt)
    # Circuit breaker trips on drawdown breach, broker errors, data staleness
    # When tripped, only risk-reducing exits are allowed (not new entries)
    if circuit_breaker:
        try:
            if circuit_breaker.is_tripped():
                trip_reason = circuit_breaker.get_trip_reason()
                logger.error(
                    f"🔴 Order blocked by CIRCUIT BREAKER: {client_order_id}",
                    extra={
                        "client_order_id": client_order_id,
                        "circuit_breaker_tripped": True,
                        "trip_reason": trip_reason,
                    },
                )
                # Gemini MEDIUM fix: Record metrics for blocked orders (observability)
                _record_order_metrics(order, start_time, "blocked")
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail={
                        "error": "Circuit breaker tripped",
                        "message": f"Trading halted due to: {trip_reason}",
                        "trip_reason": trip_reason,
                    },
                )
        except RuntimeError as e:
            # Codex HIGH fix: Circuit breaker state missing (fail-closed for safety)
            # RuntimeError is raised when circuit breaker state is missing from Redis
            # (e.g., after Redis flush/restart without explicit reinitialization)
            # Codex HIGH fix: Set unavailable flag to enable health check recovery
            recovery_manager.set_circuit_breaker_unavailable(True)
            logger.error(
                f"🔴 Order blocked by unavailable circuit breaker (FAIL CLOSED): {client_order_id}",
                extra={
                    "client_order_id": client_order_id,
                    "circuit_breaker_unavailable": True,
                    "fail_closed": True,
                    "error": str(e),
                },
            )
            _record_order_metrics(order, start_time, "blocked")
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={
                    "error": "Circuit breaker unavailable",
                    "message": "Circuit breaker state missing (fail-closed for safety)",
                    "fail_closed": True,
                },
            ) from e
        except RedisError as e:
            # Circuit breaker state unavailable (fail-closed for safety)
            # Codex HIGH fix: Set unavailable flag to enable health check recovery
            recovery_manager.set_circuit_breaker_unavailable(True)
            logger.error(
                f"🔴 Order blocked by unavailable circuit breaker (FAIL CLOSED): {client_order_id}",
                extra={
                    "client_order_id": client_order_id,
                    "circuit_breaker_unavailable": True,
                    "fail_closed": True,
                    "error": str(e),
                },
            )
            # Gemini MEDIUM fix: Record metrics for blocked orders (observability)
            _record_order_metrics(order, start_time, "blocked")
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={
                    "error": "Circuit breaker unavailable",
                    "message": "Circuit breaker state unknown (fail-closed for safety)",
                    "fail_closed": True,
                },
            ) from e

    # Reconciliation gating + quarantine enforcement
    await _check_quarantine(order.symbol, STRATEGY_ID)
    await _require_reconciliation_ready_or_reduce_only(order)

    # Position reservation for atomic limit checking (prevents race conditions)
    # Codex HIGH fix: Wire PositionReservation to prevent concurrent orders from both
    # passing position limit check before either executes
    #
    # NOTE: PositionReservation maintains its own running total of reserved positions
    # in Redis. It should be synced with actual positions during startup/reconciliation
    # using sync_position(). During order submission, we pass current_position as fallback
    # for when Redis key is missing (e.g., after Redis restart).
    reservation_token: str | None = None
    if position_reservation:
        try:
            # Codex CRITICAL fix: Get actual position from DB for fallback
            # Without this, after Redis restart the system incorrectly assumes position=0
            # which could allow orders that exceed position limits
            current_position = db_client.get_position_by_symbol(order.symbol)

            # Atomically reserve position change
            # The reservation system tracks the running total of reserved positions
            max_position_size = risk_config.position_limits.max_position_size
            reservation_result = position_reservation.reserve(
                symbol=order.symbol,
                side=order.side,
                qty=order.qty,
                max_limit=max_position_size,
                current_position=current_position,  # Fallback when Redis key missing
            )

            if not reservation_result.success:
                logger.warning(
                    f"🔴 Order blocked by position limit: {client_order_id}",
                    extra={
                        "client_order_id": client_order_id,
                        "symbol": order.symbol,
                        "side": order.side,
                        "qty": order.qty,
                        "reserved_position": reservation_result.previous_position,
                        "would_be_position": reservation_result.new_position,
                        "reason": reservation_result.reason,
                    },
                )
                _record_order_metrics(order, start_time, "blocked")
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail={
                        "error": "Position limit exceeded",
                        "message": reservation_result.reason,
                        "symbol": order.symbol,
                        "reserved_position": reservation_result.previous_position,
                        "requested_qty": order.qty,
                        "max_position_size": max_position_size,
                    },
                )

            reservation_token = reservation_result.token
            logger.debug(
                f"Position reserved: {order.symbol} {order.side} {order.qty}, "
                f"token={reservation_token}",
                extra={
                    "client_order_id": client_order_id,
                    "reservation_token": reservation_token,
                    "reserved_position": reservation_result.new_position,
                },
            )
        except HTTPException:
            raise  # Re-raise position limit errors
        except Exception as e:
            # Codex HIGH fix: Fail-closed on reservation system error
            # If Redis is unavailable, concurrent orders could race past position limits
            # Codex MEDIUM fix: Latch unavailable flag so health check reflects degraded state
            recovery_manager.set_position_reservation_unavailable(True)
            logger.error(
                f"🔴 Order blocked by reservation system failure (FAIL CLOSED): {client_order_id}",
                extra={
                    "client_order_id": client_order_id,
                    "error": str(e),
                    "fail_closed": True,
                },
            )
            _record_order_metrics(order, start_time, "blocked")
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={
                    "error": "Position reservation unavailable",
                    "message": f"Risk check system unavailable (fail-closed): {e}",
                    "fail_closed": True,
                },
            ) from e

    # Check if order already exists (idempotency)
    existing_order = db_client.get_order_by_client_id(client_order_id)
    if existing_order:
        # Release reservation since no new order is being created (idempotent return)
        if position_reservation and reservation_token:
            position_reservation.release(order.symbol, reservation_token)
            logger.debug(f"Released reservation for idempotent order: {reservation_token}")

        logger.info(
            f"Order already exists (idempotent): {client_order_id}",
            extra={"client_order_id": client_order_id, "status": existing_order.status},
        )

        # Track metrics for idempotent request (don't double-count)
        duration = time.time() - start_time
        order_placement_duration.labels(symbol=order.symbol, side=order.side).observe(duration)

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
            message=f"Order already submitted (status: {existing_order.status})",
        )

    # Fat-finger validation (size-based warnings/rejections)
    effective_thresholds = fat_finger_validator.get_effective_thresholds(order.symbol)
    if (
        effective_thresholds.max_notional is not None
        or effective_thresholds.max_qty is not None
        or effective_thresholds.max_adv_pct is not None
    ):
        price, adv = await _resolve_fat_finger_context(order, effective_thresholds)
        fat_finger_result = fat_finger_validator.validate(
            symbol=order.symbol,
            qty=order.qty,
            price=price,
            adv=adv,
            thresholds=effective_thresholds,
        )

        if fat_finger_result.breached:
            missing_fields: set[str] = set()
            for breach in fat_finger_result.breaches:
                if breach.threshold_type != "data_unavailable":
                    continue
                raw_missing = breach.metadata.get("missing", [])
                if isinstance(raw_missing, list):
                    missing_fields.update(str(item) for item in raw_missing)
            if missing_fields:
                logger.warning(
                    "Fat-finger data unavailable; treating as breach",
                    extra={
                        "client_order_id": client_order_id,
                        "symbol": order.symbol,
                        "side": order.side,
                        "qty": order.qty,
                        "missing_fields": sorted(missing_fields),
                    },
                )
            logger.warning(
                "Fat-finger threshold breached",
                extra={
                    "client_order_id": client_order_id,
                    "symbol": order.symbol,
                    "side": order.side,
                    "qty": order.qty,
                    **fat_finger_result.log_fields(),
                },
            )
            for threshold_type in iter_breach_types(fat_finger_result.breaches):
                fat_finger_warnings_total.labels(threshold_type=threshold_type).inc()

            if not DRY_RUN:
                if position_reservation and reservation_token:
                    position_reservation.release(order.symbol, reservation_token)
                    logger.debug(
                        f"Released reservation on fat-finger rejection: {reservation_token}"
                    )

                for threshold_type in iter_breach_types(fat_finger_result.breaches):
                    fat_finger_rejections_total.labels(threshold_type=threshold_type).inc()

                _record_order_metrics(order, start_time, "blocked")

                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail={
                        "error": "Fat-finger threshold exceeded",
                        "message": "Order rejected due to size safety checks",
                        **fat_finger_result.to_response(),
                    },
                )

    # Submit order based on DRY_RUN mode
    if DRY_RUN:
        # DRY_RUN mode - log order but don't submit to broker
        logger.info(
            f"[DRY_RUN] Logging order: {order.symbol} {order.side} {order.qty}",
            extra={"client_order_id": client_order_id},
        )

        # C4 Fix: Handle race condition where concurrent requests both pass idempotency check
        try:
            order_detail = db_client.create_order(
                client_order_id=client_order_id,
                strategy_id=STRATEGY_ID,
                order_request=order,
                status="dry_run",
                broker_order_id=None,
            )
        except UniqueViolation:
            # Release reservation on race condition (another request won)
            if position_reservation and reservation_token:
                position_reservation.release(order.symbol, reservation_token)
            return _handle_idempotency_race(client_order_id, db_client)
        except Exception as e:
            # Codex MEDIUM fix: Release reservation on any DB error to prevent leaks
            # Other DB errors (connection drop, constraint failure, serialization error)
            # would otherwise leak the reservation until TTL expiry
            if position_reservation and reservation_token:
                position_reservation.release(order.symbol, reservation_token)
                logger.debug(f"Released reservation on DRY_RUN DB error: {reservation_token}")
            logger.error(f"DRY_RUN order DB error: {e}", exc_info=True)
            _record_order_metrics(order, start_time, "failed")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to save DRY_RUN order: {str(e)}",
            ) from e

        # Confirm position reservation after successful order creation
        if position_reservation and reservation_token:
            position_reservation.confirm(order.symbol, reservation_token)
            logger.debug(f"Confirmed reservation for DRY_RUN order: {reservation_token}")

        # Track metrics for dry run order
        _record_order_metrics(order, start_time, "success")

        return OrderResponse(
            client_order_id=client_order_id,
            status="dry_run",
            broker_order_id=None,
            symbol=order.symbol,
            side=order.side,
            qty=order.qty,
            order_type=order.order_type,
            limit_price=order.limit_price,
            created_at=order_detail.created_at,
            message="Order logged (DRY_RUN mode)",
        )

    else:
        # Live mode - submit to Alpaca
        if not alpaca_client:
            # Release reservation since we can't submit to broker
            if position_reservation and reservation_token:
                position_reservation.release(order.symbol, reservation_token)
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Alpaca client not initialized. Check credentials.",
            )

        try:
            # Submit to Alpaca with retry logic
            alpaca_api_status = "success"
            try:
                alpaca_response = alpaca_client.submit_order(order, client_order_id)
            except Exception:
                alpaca_api_status = "error"
                raise
            finally:
                # Always track Alpaca API request metric
                alpaca_api_requests_total.labels(
                    operation="submit_order", status=alpaca_api_status
                ).inc()

            # C4 Fix: Handle race condition in live mode
            # Alpaca handles duplicate client_order_ids idempotently
            # But we need to handle the DB race condition
            try:
                order_detail = db_client.create_order(
                    client_order_id=client_order_id,
                    strategy_id=STRATEGY_ID,
                    order_request=order,
                    status=alpaca_response["status"],
                    broker_order_id=alpaca_response["id"],
                )
            except UniqueViolation:
                # Release reservation on race condition (another request won)
                if position_reservation and reservation_token:
                    position_reservation.release(order.symbol, reservation_token)
                return _handle_idempotency_race(client_order_id, db_client)

            # Confirm position reservation after successful broker submission
            if position_reservation and reservation_token:
                position_reservation.confirm(order.symbol, reservation_token)
                logger.debug(f"Confirmed reservation for live order: {reservation_token}")

            logger.info(
                f"Order submitted to Alpaca: {client_order_id}",
                extra={
                    "client_order_id": client_order_id,
                    "broker_order_id": alpaca_response["id"],
                    "status": alpaca_response["status"],
                },
            )

            # Track metrics for successful order submission
            _record_order_metrics(order, start_time, "success")

            return OrderResponse(
                client_order_id=client_order_id,
                status=alpaca_response["status"],
                broker_order_id=alpaca_response["id"],
                symbol=order.symbol,
                side=order.side,
                qty=order.qty,
                order_type=order.order_type,
                limit_price=order.limit_price,
                created_at=order_detail.created_at,
                message="Order submitted to broker",
            )

        except (AlpacaValidationError, AlpacaRejectionError, AlpacaConnectionError):
            # Release reservation on broker rejection/error
            if position_reservation and reservation_token:
                position_reservation.release(order.symbol, reservation_token)
                logger.debug(f"Released reservation on broker error: {reservation_token}")
            # Track metrics for rejected orders
            _record_order_metrics(order, start_time, "rejected")
            # These will be handled by exception handlers
            raise

        except Exception as e:
            # Release reservation on unexpected error
            if position_reservation and reservation_token:
                position_reservation.release(order.symbol, reservation_token)
                logger.debug(f"Released reservation on unexpected error: {reservation_token}")

            logger.error(f"Unexpected error submitting order: {e}", exc_info=True)

            # Save failed order to database
            db_client.create_order(
                client_order_id=client_order_id,
                strategy_id=STRATEGY_ID,
                order_request=order,
                status="rejected",
                broker_order_id=None,
                error_message=str(e),
            )

            # Track metrics for failed orders
            _record_order_metrics(order, start_time, "failed")

            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Order submission failed: {str(e)}",
            ) from e


@app.post("/api/v1/orders/{client_order_id}/cancel", tags=["Orders"])
async def cancel_order(
    client_order_id: str,
    response: Response,
    # IMPORTANT: Auth must run BEFORE rate limiting to populate request.state with user context
    # This allows rate limiter to bucket by user/service instead of anonymous IP
    _auth_context: AuthContext = Depends(order_cancel_auth),
    _rate_limit_remaining: int = Depends(order_cancel_rl),
) -> dict[str, Any]:
    """Cancel a single order by client_order_id."""
    order = db_client.get_order_by_client_id(client_order_id)
    if not order:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Order not found: {client_order_id}",
        )

    if order.status in TERMINAL_STATUSES:
        return {
            "client_order_id": client_order_id,
            "status": order.status,
            "message": "Order already in terminal state",
        }

    if not DRY_RUN:
        if not alpaca_client:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Alpaca client not initialized. Check credentials.",
            )
        if order.broker_order_id:
            alpaca_client.cancel_order(order.broker_order_id)

    updated = db_client.update_order_status_cas(
        client_order_id=client_order_id,
        status="canceled",
        broker_updated_at=datetime.now(UTC),
        status_rank=status_rank_for("canceled"),
        source_priority=SOURCE_PRIORITY_MANUAL,
        filled_qty=order.filled_qty,
        filled_avg_price=order.filled_avg_price,
        filled_at=order.filled_at,
        broker_order_id=order.broker_order_id,
    )

    return {
        "client_order_id": client_order_id,
        "status": updated.status if updated else "canceled",
        "message": "Order canceled",
    }


# ============================================================================
# TWAP Order Helper Functions
# ============================================================================


def _check_twap_prerequisites() -> None:
    """
    Check prerequisites for TWAP order submission.

    Validates that slice scheduler is available and kill-switch allows trading.
    Follows fail-closed principle for kill-switch unavailability.

    Raises:
        HTTPException 503: If scheduler unavailable or kill-switch state unknown
        HTTPException 503: If kill-switch is engaged
    """
    slice_scheduler = recovery_manager.slice_scheduler
    kill_switch = recovery_manager.kill_switch

    # Check if slice scheduler is available
    if not slice_scheduler:
        logger.error("Slice scheduler unavailable - cannot accept TWAP orders")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="TWAP order service unavailable (scheduler not initialized)",
        )

    # Check kill-switch availability (fail closed)
    if recovery_manager.is_kill_switch_unavailable() or kill_switch is None:
        logger.error("Kill-switch unavailable - cannot accept TWAP orders (fail closed)")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="TWAP order service unavailable (kill-switch state unknown)",
        )

    # Check kill-switch status
    if kill_switch.is_engaged():
        status_info = kill_switch.get_status()
        logger.error("TWAP order blocked by kill-switch")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "error": "Kill-switch engaged",
                "message": "All trading halted by operator",
                "engaged_by": status_info.get("engaged_by"),
                "reason": status_info.get("engagement_reason"),
            },
        )


def _convert_slices_to_details(
    slices: list[OrderDetail], parent_order_id: str
) -> list[SliceDetail]:
    """
    Convert OrderDetail list to SliceDetail list for response.

    Validates that each slice has required slice_num and scheduled_time fields.
    Raises HTTPException if data corruption detected.

    Args:
        slices: List of OrderDetail from database
        parent_order_id: Parent order ID for error logging

    Returns:
        List of SliceDetail for API response

    Raises:
        HTTPException 500: If slice data is corrupt (missing slice_num or scheduled_time)
    """
    slice_details = []
    for s in slices:
        # Child slices must have slice_num and scheduled_time
        # If these are None, it indicates data corruption
        if s.slice_num is None or s.scheduled_time is None:
            logger.error(
                f"Corrupt slice data for parent {parent_order_id}: "
                f"slice_num or scheduled_time is None for client_order_id={s.client_order_id}",
                extra={
                    "parent_order_id": parent_order_id,
                    "client_order_id": s.client_order_id,
                    "slice_num": s.slice_num,
                    "scheduled_time": s.scheduled_time,
                },
            )
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Corrupt slice data found in database",
            ) from None

        slice_details.append(
            SliceDetail(
                slice_num=s.slice_num,
                qty=s.qty,
                scheduled_time=s.scheduled_time,
                client_order_id=s.client_order_id,
                strategy_id=s.strategy_id,
                status=s.status,
            )
        )
    return slice_details


def _find_existing_twap_plan(
    request: SlicingRequest, slicing_plan: SlicingPlan, trade_date: date
) -> SlicingPlan | None:
    """
    Check for existing TWAP order (idempotency + backward compatibility).

    First checks new hash format (with duration), then legacy hash (without duration)
    for backward compatibility with pre-fix orders. If legacy order found, validates
    duration matches before returning to prevent hash collisions.

    Args:
        request: TWAP order request
        slicing_plan: Generated slicing plan with parent_order_id
        trade_date: Consistent trade date for idempotency

    Returns:
        SlicingPlan if existing order found, None if new order needed

    Raises:
        HTTPException 500: If corrupt slice data found
    """
    # Check if parent order already exists (idempotency)
    # First check new hash (with duration), then legacy hash (backward compatibility)
    existing_parent = db_client.get_order_by_client_id(slicing_plan.parent_order_id)

    if existing_parent:
        # Use the strategy_id from the DB to ensure consistency
        slicing_plan.parent_strategy_id = existing_parent.strategy_id
    else:
        # Legacy TWAP plans implicitly used 60-second spacing. If the caller is requesting
        # a different interval we must skip fallback checks to avoid returning an order
        # with mismatched pacing metadata.
        if request.interval_seconds != LEGACY_TWAP_INTERVAL_SECONDS:
            logger.debug(
                "Skipping legacy TWAP hash fallback for non-default interval",
                extra={
                    "requested_interval_seconds": request.interval_seconds,
                    "legacy_interval_seconds": LEGACY_TWAP_INTERVAL_SECONDS,
                },
            )
            return None

        # Backward compatibility: check prior hash formats (without interval and/or duration)
        # CRITICAL: Use same trade_date for idempotency across midnight
        requested_total_slices = slicing_plan.total_slices
        fallback_strategies = [
            (
                f"twap_parent_{request.duration_minutes}m",
                "duration-based legacy hash",
            ),
            (
                "twap_parent",
                "pre-duration legacy hash",
            ),
        ]

        for strategy_id, label in fallback_strategies:
            legacy_parent_id = reconstruct_order_params_hash(
                symbol=request.symbol,
                side=request.side,
                qty=request.qty,
                limit_price=request.limit_price,
                stop_price=request.stop_price,
                order_type=request.order_type,
                time_in_force=request.time_in_force,
                strategy_id=strategy_id,
                order_date=trade_date,
            )
            legacy_parent = db_client.get_order_by_client_id(legacy_parent_id)

            if not legacy_parent:
                continue

            if legacy_parent.total_slices == requested_total_slices:
                logger.info(
                    "Found %s TWAP order: legacy_id=%s",
                    label,
                    legacy_parent_id,
                    extra={
                        "legacy_parent_id": legacy_parent_id,
                        "new_parent_id": slicing_plan.parent_order_id,
                        "status": legacy_parent.status,
                        "total_slices": legacy_parent.total_slices,
                    },
                )
                slicing_plan.parent_order_id = legacy_parent_id
                slicing_plan.parent_strategy_id = legacy_parent.strategy_id
                existing_parent = legacy_parent
                break

            logger.info(
                "Legacy TWAP order found but slice count differs: legacy_total_slices=%s, "
                "requested_total_slices=%s. Creating new order with new hash.",
                legacy_parent.total_slices,
                requested_total_slices,
                extra={
                    "legacy_parent_id": legacy_parent_id,
                    "new_parent_id": slicing_plan.parent_order_id,
                    "legacy_total_slices": legacy_parent.total_slices,
                    "requested_total_slices": requested_total_slices,
                },
            )

    if not existing_parent:
        return None

    # Existing order found - return it (idempotent response)
    logger.info(
        f"TWAP order already exists (idempotent): parent={slicing_plan.parent_order_id}",
        extra={
            "parent_order_id": slicing_plan.parent_order_id,
            "status": existing_parent.status,
            "total_slices": existing_parent.total_slices,
        },
    )

    # Fetch all child slices to return complete plan
    existing_slices = db_client.get_slices_by_parent_id(slicing_plan.parent_order_id)
    slice_details = _convert_slices_to_details(existing_slices, slicing_plan.parent_order_id)

    # Return existing slicing plan (idempotent response)
    return SlicingPlan(
        parent_order_id=slicing_plan.parent_order_id,
        parent_strategy_id=slicing_plan.parent_strategy_id,
        symbol=request.symbol,
        side=request.side,
        total_qty=request.qty,
        total_slices=len(slice_details),
        duration_minutes=request.duration_minutes,
        interval_seconds=slicing_plan.interval_seconds,
        slices=slice_details,
    )


def _create_twap_in_db(
    request: SlicingRequest,
    slicing_plan: SlicingPlan,
    parent_metadata: dict[str, Any] | None,
) -> SlicingPlan | None:
    """
    Create parent + child orders atomically in database.

    Uses database transaction for all-or-nothing behavior. Handles race condition
    where concurrent identical requests both pass idempotency check: catches
    UniqueViolation and returns existing plan instead of 500 error.

    Args:
        request: TWAP order request
        slicing_plan: Generated slicing plan
        parent_metadata: Optional metadata to persist with the parent order

    Returns:
        SlicingPlan if concurrent submission detected, None if created successfully

    Raises:
        HTTPException 500: If database inconsistency after UniqueViolation
    """
    # 🔒 CRITICAL: Create parent + child orders atomically (defense against partial writes)
    # Use database transaction to ensure all-or-nothing behavior. If any insert fails,
    # the entire TWAP order creation rolls back to prevent orphaned parent orders.
    #
    # 🔒 RACE CONDITION DEFENSE: Handle concurrent submissions with identical client_order_ids.
    # Two simultaneous requests can both pass the pre-transaction idempotency check and attempt
    # to insert. The second insert will fail with UniqueViolation. We catch this and return
    # the existing plan to make concurrent submissions deterministic and idempotent.
    try:
        with db_client.transaction() as conn:
            # Create parent order in database
            parent_order_request = OrderRequest(
                symbol=request.symbol,
                side=request.side,
                qty=request.qty,
                order_type=request.order_type,
                limit_price=request.limit_price,
                stop_price=request.stop_price,
                time_in_force=request.time_in_force,
            )
            db_client.create_parent_order(
                client_order_id=slicing_plan.parent_order_id,
                strategy_id=slicing_plan.parent_strategy_id,  # Use strategy_id from plan
                order_request=parent_order_request,
                total_slices=slicing_plan.total_slices,
                metadata=parent_metadata,
                conn=conn,  # Use shared transaction connection
            )

            # Create child slice orders in database
            for slice_detail in slicing_plan.slices:
                slice_order_request = OrderRequest(
                    symbol=request.symbol,
                    side=request.side,
                    qty=slice_detail.qty,
                    order_type=request.order_type,
                    limit_price=request.limit_price,
                    stop_price=request.stop_price,
                    time_in_force=request.time_in_force,
                )
                db_client.create_child_slice(
                    client_order_id=slice_detail.client_order_id,
                    parent_order_id=slicing_plan.parent_order_id,
                    slice_num=slice_detail.slice_num,
                    strategy_id=slice_detail.strategy_id,  # Use strategy_id from slice details
                    order_request=slice_order_request,
                    scheduled_time=slice_detail.scheduled_time,
                    conn=conn,  # Use shared transaction connection
                )
            # Transaction auto-commits on successful context exit
    except UniqueViolation:
        # Concurrent submission detected: Another request created this parent_order_id
        # between our idempotency check and transaction commit. Fetch and return the
        # existing plan to provide deterministic, idempotent response without 500 error.
        logger.info(
            "Concurrent TWAP submission detected (UniqueViolation): "
            f"parent={slicing_plan.parent_order_id}. Returning existing plan.",
            extra={
                "parent_order_id": slicing_plan.parent_order_id,
                "symbol": request.symbol,
                "side": request.side,
                "qty": request.qty,
            },
        )

        # Fetch existing parent and slices
        existing_parent = db_client.get_order_by_client_id(slicing_plan.parent_order_id)
        if not existing_parent:
            # Should never happen: UniqueViolation means the parent exists
            logger.error(
                "UniqueViolation raised but parent order not found: "
                f"{slicing_plan.parent_order_id}",
                extra={"parent_order_id": slicing_plan.parent_order_id},
            )
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Database inconsistency: parent order not found after UniqueViolation",
            ) from None

        # Use the strategy_id from the DB to ensure consistency
        slicing_plan.parent_strategy_id = existing_parent.strategy_id

        existing_slices = db_client.get_slices_by_parent_id(slicing_plan.parent_order_id)
        slice_details = _convert_slices_to_details(existing_slices, slicing_plan.parent_order_id)

        # Return existing plan (idempotent response for concurrent submission)
        return SlicingPlan(
            parent_order_id=slicing_plan.parent_order_id,
            parent_strategy_id=slicing_plan.parent_strategy_id,
            symbol=request.symbol,
            side=request.side,
            total_qty=request.qty,
            total_slices=len(slice_details),
            duration_minutes=request.duration_minutes,
            interval_seconds=slicing_plan.interval_seconds,
            slices=slice_details,
        )

    # Successfully created in database
    return None


def _schedule_slices_with_compensation(
    request: SlicingRequest, slicing_plan: SlicingPlan
) -> list[str]:
    """
    Schedule slices for execution with failure compensation.

    Schedules all slices using APScheduler. If scheduling fails after database
    commit, compensates by canceling pending slices. Uses defense-in-depth:
    only cancels slices still in 'pending_new' status to avoid race conditions.

    Args:
        request: TWAP order request
        slicing_plan: Slicing plan with parent and child orders

    Returns:
        List of APScheduler job IDs

    Raises:
        Exception: Re-raises scheduling errors after compensation attempt
    """
    slice_scheduler = recovery_manager.slice_scheduler
    assert slice_scheduler is not None, "Slice scheduler must be initialized"

    # Schedule slices for execution
    # Note: Scheduling happens AFTER transaction commit, so we must compensate if it fails
    try:
        job_ids = slice_scheduler.schedule_slices(
            parent_order_id=slicing_plan.parent_order_id,
            slices=slicing_plan.slices,
            symbol=request.symbol,
            side=request.side,
            order_type=request.order_type,
            limit_price=request.limit_price,
            stop_price=request.stop_price,
            time_in_force=request.time_in_force,
        )
        return job_ids
    except Exception as e:
        # Scheduling failed after DB commit - compensate by canceling created orders
        logger.error(
            "Scheduling failed for parent=%s, compensating by canceling " "pending orders",
            slicing_plan.parent_order_id,
            extra={
                "parent_order_id": slicing_plan.parent_order_id,
                "error": str(e),
                "error_type": type(e).__name__,
            },
        )
        # Cancel only orders still in 'pending_new' status to avoid race conditions
        # (First slice scheduled for "now" may have already executed and submitted to broker)
        try:
            # Cancel all child slices still in pending_new status
            canceled_count = db_client.cancel_pending_slices(slicing_plan.parent_order_id)

            # Check if any child slices have already progressed past pending_new
            all_slices = db_client.get_slices_by_parent_id(slicing_plan.parent_order_id)
            progressed_slices = [
                s for s in all_slices if s.status != "pending_new" and s.status != "canceled"
            ]

            if not progressed_slices:
                # All slices still pending or canceled - safe to cancel parent
                db_client.update_order_status(
                    client_order_id=slicing_plan.parent_order_id,
                    status="canceled",
                    error_message=f"Scheduling failed: {str(e)}",
                )
                logger.info(
                    "Compensated scheduling failure: canceled parent and "
                    f"{canceled_count} pending slices",
                    extra={
                        "parent_order_id": slicing_plan.parent_order_id,
                        "canceled_slices": canceled_count,
                        "total_slices": len(all_slices),
                    },
                )
            else:
                # Some slices already submitted/executing - don't cancel
                # parent to avoid inconsistency
                progressed_statuses = [s.status for s in progressed_slices]
                logger.warning(
                    f"Scheduling partially failed but {len(progressed_slices)} "
                    f"slices already progressed (statuses: {progressed_statuses}). "
                    f"Canceled {canceled_count} pending slices but leaving "
                    "parent active to track live orders.",
                    extra={
                        "parent_order_id": slicing_plan.parent_order_id,
                        "canceled_slices": canceled_count,
                        "progressed_slices": len(progressed_slices),
                        "progressed_statuses": [s.status for s in progressed_slices],
                    },
                )
        except Exception as cleanup_error:
            logger.error(
                f"Cleanup failed after scheduling error: {cleanup_error}",
                extra={"parent_order_id": slicing_plan.parent_order_id},
            )
        # Re-raise original scheduling error
        raise


@app.post("/api/v1/orders/slice", response_model=SlicingPlan, tags=["Orders"])
async def submit_sliced_order(
    request: SlicingRequest,
    response: Response,
    # IMPORTANT: Auth must run BEFORE rate limiting to populate request.state with user context
    # This allows rate limiter to bucket by user/service instead of anonymous IP
    _auth_context: AuthContext = Depends(order_slice_auth),
    _rate_limit_remaining: int = Depends(order_slice_rl),
) -> SlicingPlan:
    """
    Submit TWAP order with automatic slicing and scheduled execution.

    Creates a parent order and multiple child slice orders distributed evenly
    over the specified duration. Each slice is scheduled for execution at the
    requested interval spacing with mandatory safety guards (kill switch,
    circuit breaker checks).

    Args:
        request: TWAP slicing request (symbol, side, qty, duration, etc.)

    Returns:
        SlicingPlan with parent_order_id and list of scheduled slices

    Raises:
        HTTPException 400: Invalid request parameters
        HTTPException 503: Required services unavailable (scheduler, kill-switch, etc.)
        HTTPException 500: Database or scheduling error

    Examples:
        Market order TWAP:
        >>> import requests
        >>> response = requests.post(
        ...     "http://localhost:8002/api/v1/orders/slice",
        ...     json={
        ...         "symbol": "AAPL",
        ...         "side": "buy",
        ...         "qty": 100,
        ...         "duration_minutes": 5,
        ...         "order_type": "market"
        ...     }
        ... )
        >>> response.json()
        {
            "parent_order_id": "abc123...",
            "symbol": "AAPL",
            "side": "buy",
            "total_qty": 100,
            "total_slices": 5,
            "duration_minutes": 5,
            "slices": [
                {"slice_num": 0, "qty": 20, "scheduled_time": "...", ...},
                {"slice_num": 1, "qty": 20, "scheduled_time": "...", ...},
                ...
            ]
        }
    """
    # Step 1: Log request (before prerequisite checks for observability)
    logger.info(
        f"TWAP order request: {request.symbol} {request.side} {request.qty} "
        f"over {request.duration_minutes} min",
        extra={
            "symbol": request.symbol,
            "side": request.side,
            "qty": request.qty,
            "duration_minutes": request.duration_minutes,
            "interval_seconds": request.interval_seconds,
        },
    )

    # Step 2: Check prerequisites (scheduler availability, kill-switch)
    _check_twap_prerequisites()

    # Reconciliation gating (no reduce-only path for TWAP)
    await _check_quarantine(request.symbol, STRATEGY_ID)
    if not _is_reconciliation_ready():
        if reconciliation_service and reconciliation_service.override_active():
            logger.warning(
                "Reconciliation override active; allowing TWAP order",
                extra={
                    "symbol": request.symbol,
                    "override": reconciliation_service.override_context(),
                },
            )
        else:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Reconciliation in progress - TWAP orders blocked",
            )

    try:
        # CRITICAL: Use consistent trade_date for idempotency across midnight
        # If client retries after midnight, must pass same trade_date to avoid duplicate orders
        trade_date = request.trade_date or datetime.now(UTC).date()

        # Step 3: Apply liquidity constraints (ADV-based) before slicing
        adv_20d: int | None = None
        max_slice_qty: int | None = None
        if LIQUIDITY_CHECK_ENABLED:
            if liquidity_service is None:
                logger.warning(
                    "Liquidity check enabled but service unavailable; rejecting TWAP request",
                    extra={"symbol": request.symbol},
                )
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail="Liquidity service unavailable; please retry",
                )
            else:
                adv_20d = await asyncio.to_thread(liquidity_service.get_adv, request.symbol)
                if adv_20d is None:
                    logger.warning(
                        "ADV lookup unavailable with no cache; rejecting TWAP "
                        "request to preserve idempotency",
                        extra={"symbol": request.symbol},
                    )
                    raise HTTPException(
                        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                        detail="Liquidity data unavailable (ADV lookup failed); please retry",
                    )
                computed = int(adv_20d * MAX_SLICE_PCT_OF_ADV)
                if computed < 1:
                    logger.warning(
                        "Computed max_slice_qty < 1; clamping to 1 share",
                        extra={
                            "symbol": request.symbol,
                            "adv_20d": adv_20d,
                            "max_slice_pct_of_adv": MAX_SLICE_PCT_OF_ADV,
                            "computed": computed,
                            "clamped": 1,
                        },
                    )
                max_slice_qty = max(1, computed)

        liquidity_constraints: dict[str, bool | int | float | str | None] = {
            "enabled": LIQUIDITY_CHECK_ENABLED,
            "adv_20d": adv_20d,
            "max_slice_pct_of_adv": MAX_SLICE_PCT_OF_ADV,
            "max_slice_qty": max_slice_qty,
        }
        if adv_20d is not None:
            liquidity_constraints["calculated_at"] = datetime.now(UTC).isoformat()
            liquidity_constraints["source"] = "alpaca_bars_20d"

        # Step 4: Create slicing plan with consistent trade_date
        slicing_plan = twap_slicer.plan(
            symbol=request.symbol,
            side=request.side,
            qty=request.qty,
            duration_minutes=request.duration_minutes,
            interval_seconds=request.interval_seconds,
            max_slice_qty=max_slice_qty,
            order_type=request.order_type,
            limit_price=request.limit_price,
            stop_price=request.stop_price,
            time_in_force=request.time_in_force,
            trade_date=trade_date,  # Pass consistent trade_date
        )

        # Step 5: Check for existing order (idempotency + backward compatibility)
        existing_plan = _find_existing_twap_plan(request, slicing_plan, trade_date)
        if existing_plan:
            return existing_plan

        # Step 6: Create parent + child orders atomically in database
        # Handles concurrent submissions by catching UniqueViolation
        concurrent_plan = _create_twap_in_db(
            request, slicing_plan, {"liquidity_constraints": liquidity_constraints}
        )
        if concurrent_plan:
            return concurrent_plan

        # Step 7: Schedule slices for execution with failure compensation
        job_ids = _schedule_slices_with_compensation(request, slicing_plan)

        # Step 8: Log success and return
        logger.info(
            f"TWAP order created: parent={slicing_plan.parent_order_id}, slices={len(job_ids)}",
            extra={
                "parent_order_id": slicing_plan.parent_order_id,
                "total_slices": len(job_ids),
                "symbol": request.symbol,
            },
        )

        return slicing_plan
    except ValueError as e:
        # Validation error from slicer
        logger.error(f"TWAP validation error: {e}", extra={"error": str(e)})
        # Re-raise with 'from e' to preserve original traceback for debugging
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e)) from e
    except Exception as e:
        # Database or scheduling error
        logger.error(f"TWAP order creation failed: {e}", extra={"error": str(e)})
        # Re-raise with 'from e' to preserve original traceback for debugging
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"TWAP order creation failed: {str(e)}",
        ) from e


@app.get("/api/v1/orders/{parent_id}/slices", response_model=list[OrderDetail], tags=["Orders"])
async def get_slices_by_parent(
    parent_id: str,
    _auth_context: AuthContext = Depends(order_read_auth),
) -> list[OrderDetail]:
    """
    Get all child slices for a parent TWAP order.

    Retrieves all child slice orders (both pending and executed) for a given
    parent order ID, ordered by slice number.

    Args:
        parent_id: Parent order's client_order_id

    Returns:
        List of OrderDetail for all child slices (ordered by slice_num)

    Raises:
        HTTPException 404: Parent order not found
        HTTPException 500: Database error

    Examples:
        >>> import requests
        >>> response = requests.get(
        ...     "http://localhost:8002/api/v1/orders/parent123/slices"
        ... )
        >>> response.json()
        [
            {"client_order_id": "slice0_abc...", "slice_num": 0, "status": "filled", ...},
            {"client_order_id": "slice1_def...", "slice_num": 1, "status": "pending_new", ...},
            ...
        ]
    """
    try:
        slices = db_client.get_slices_by_parent_id(parent_id)
        if not slices:
            # Check if parent exists
            parent = db_client.get_order_by_client_id(parent_id)
            if not parent:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Parent order not found: {parent_id}",
                )
            # Parent exists but has no slices (shouldn't happen for TWAP orders)
            return []
        return slices
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to fetch slices for parent {parent_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to fetch slices: {str(e)}",
        ) from e


@app.delete("/api/v1/orders/{parent_id}/slices", tags=["Orders"])
async def cancel_slices(
    parent_id: str,
    _auth_context: AuthContext = Depends(order_cancel_auth),
) -> dict[str, Any]:
    """
    Cancel all pending child slices for a parent TWAP order.

    Removes scheduled jobs from the scheduler and updates database to mark
    all pending_new slices as canceled. Already-executed slices are not affected.

    Args:
        parent_id: Parent order's client_order_id

    Returns:
        Dictionary with cancellation counts

    Raises:
        HTTPException 404: Parent order not found
        HTTPException 503: Scheduler unavailable
        HTTPException 500: Cancellation error

    Examples:
        >>> import requests
        >>> response = requests.delete(
        ...     "http://localhost:8002/api/v1/orders/parent123/slices"
        ... )
        >>> response.json()
        {
            "parent_order_id": "parent123",
            "scheduler_canceled": 3,
            "db_canceled": 3,
            "message": "Canceled 3 pending slices"
        }
    """
    slice_scheduler = recovery_manager.slice_scheduler

    # Check scheduler availability
    if not slice_scheduler:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Slice scheduler unavailable - cannot cancel slices",
        )

    # Check if parent exists
    parent = db_client.get_order_by_client_id(parent_id)
    if not parent:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"Parent order not found: {parent_id}"
        )

    try:
        # Cancel remaining slices (removes from scheduler + updates DB)
        # Note: SliceScheduler updates DB first, then removes scheduler jobs
        scheduler_canceled_count, db_canceled_count = slice_scheduler.cancel_remaining_slices(
            parent_id
        )

        logger.info(
            f"Canceled slices for parent {parent_id}: "
            f"scheduler={scheduler_canceled_count}, db={db_canceled_count}",
            extra={
                "parent_order_id": parent_id,
                "scheduler_canceled": scheduler_canceled_count,
                "db_canceled": db_canceled_count,
            },
        )

        return {
            "parent_order_id": parent_id,
            "scheduler_canceled": scheduler_canceled_count,
            "db_canceled": db_canceled_count,
            "message": (
                f"Canceled {db_canceled_count} pending slices in DB, "
                f"removed {scheduler_canceled_count} jobs from scheduler"
            ),
        }
    except Exception as e:
        logger.error(f"Failed to cancel slices for parent {parent_id}: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to cancel slices: {str(e)}",
        ) from e


@app.get("/api/v1/orders/{client_order_id}", response_model=OrderDetail, tags=["Orders"])
async def get_order(
    client_order_id: str,
    _auth_context: AuthContext = Depends(order_read_auth),
) -> OrderDetail:
    """
    Get order details by client_order_id.

    Args:
        client_order_id: Deterministic client order ID

    Returns:
        OrderDetail with full order information

    Raises:
        HTTPException 404: Order not found

    Examples:
        >>> import requests
        >>> response = requests.get(
        ...     "http://localhost:8002/api/v1/orders/a1b2c3d4e5f6..."
        ... )
        >>> response.json()
        {
            "client_order_id": "a1b2c3d4e5f6...",
            "strategy_id": "alpha_baseline",
            "symbol": "AAPL",
            "side": "buy",
            "qty": 10,
            "order_type": "market",
            "status": "filled",
            "broker_order_id": "broker123...",
            "filled_qty": "10",
            "filled_avg_price": "150.25",
            "created_at": "2024-10-17T16:30:00Z",
            "filled_at": "2024-10-17T16:30:05Z"
        }
    """
    order = db_client.get_order_by_client_id(client_order_id)

    if not order:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"Order not found: {client_order_id}"
        )

    return order


@app.get("/api/v1/positions", response_model=PositionsResponse, tags=["Positions"])
async def get_positions(
    _auth_context: AuthContext = Depends(order_read_auth),
) -> PositionsResponse:
    """
    Get all current positions.

    Returns list of positions with P&L calculations.

    Returns:
        PositionsResponse with list of positions

    Examples:
        >>> import requests
        >>> response = requests.get("http://localhost:8002/api/v1/positions")
        >>> response.json()
        {
            "positions": [
                {
                    "symbol": "AAPL",
                    "qty": "10",
                    "avg_entry_price": "150.25",
                    "current_price": "152.75",
                    "unrealized_pl": "25.00",
                    "realized_pl": "0.00",
                    "updated_at": "2024-10-17T16:30:00Z"
                }
            ],
            "total_positions": 1,
            "total_unrealized_pl": "25.00",
            "total_realized_pl": "0.00"
        }
    """
    positions = db_client.get_all_positions()

    # Protect access to the shared `_tracked_position_symbols` set and fix memory leak
    async with _position_metrics_lock:
        current_symbols = {pos.symbol for pos in positions}

        # Reset metrics for symbols that are no longer in our portfolio
        symbols_to_remove = _tracked_position_symbols - current_symbols
        for symbol in symbols_to_remove:
            positions_current.labels(symbol=symbol).set(0)

        # Update position metrics for each current symbol
        for pos in positions:
            positions_current.labels(symbol=pos.symbol).set(float(pos.qty))

        # Update the set of tracked symbols to match the current portfolio
        _tracked_position_symbols.clear()
        _tracked_position_symbols.update(current_symbols)

    # Calculate totals
    total_unrealized_pl = sum(
        ((pos.unrealized_pl or Decimal("0")) for pos in positions), Decimal("0")
    )
    total_realized_pl = sum((pos.realized_pl for pos in positions), Decimal("0"))

    return PositionsResponse(
        positions=positions,
        total_positions=len(positions),
        total_unrealized_pl=total_unrealized_pl if positions else None,
        total_realized_pl=total_realized_pl,
    )


@app.get("/api/v1/performance/daily", response_model=DailyPerformanceResponse, tags=["Performance"])
@require_permission(Permission.VIEW_PNL)
async def get_daily_performance(
    request: Request,
    start_date: date = Query(default_factory=lambda: date.today() - timedelta(days=30)),
    end_date: date = Query(default_factory=date.today),
    user: dict[str, Any] = Depends(_build_user_context),
) -> DailyPerformanceResponse:
    """Daily realized P&L (equity & drawdown) for performance dashboard."""

    if not FEATURE_PERFORMANCE_DASHBOARD:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Performance dashboard disabled"
        )

    perf_request = PerformanceRequest(start_date=start_date, end_date=end_date)
    authorized_strategies = get_authorized_strategies(user.get("user"))
    requested_strategies = cast(
        list[str], user.get("requested_strategies", []) if isinstance(user, dict) else []
    )
    user_id = user.get("user_id") if isinstance(user, dict) else None
    if not authorized_strategies:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="No strategy access")
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Missing user id for RBAC"
        )

    invalid_strategies = set(requested_strategies) - set(authorized_strategies)
    if invalid_strategies:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Strategy access denied: {sorted(invalid_strategies)}",
        )

    effective_strategies = requested_strategies or authorized_strategies
    if not effective_strategies:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="No strategy access")

    cache_key = _performance_cache_key(
        perf_request.start_date, perf_request.end_date, tuple(effective_strategies), user_id
    )

    # Serve from cache if available; scoped to user+strategies
    if redis_client:
        try:
            cached = redis_client.get(cache_key)
            if cached:
                return DailyPerformanceResponse.model_validate_json(cached)
        except Exception as e:
            logger.warning(f"Performance cache read failed: {e}")

    rows = db_client.get_daily_pnl_history(
        perf_request.start_date, perf_request.end_date, effective_strategies
    )
    daily, total_realized, max_drawdown = _compute_daily_performance(
        rows, perf_request.start_date, perf_request.end_date
    )

    data_available_from = db_client.get_data_availability_date()

    response = DailyPerformanceResponse(
        daily_pnl=daily,
        total_realized_pl=total_realized,
        max_drawdown_pct=max_drawdown,
        start_date=perf_request.start_date,
        end_date=perf_request.end_date,
        data_available_from=data_available_from,
        last_updated=datetime.now(UTC),
    )

    # Cache response and register index for targeted invalidation
    if redis_client:
        try:
            redis_client.set(cache_key, response.model_dump_json(), ttl=PERFORMANCE_CACHE_TTL)
            _register_performance_cache(cache_key, perf_request.start_date, perf_request.end_date)
        except Exception as e:
            logger.warning(f"Performance cache write failed: {e}")

    return response


@app.get("/api/v1/positions/pnl/realtime", response_model=RealtimePnLResponse, tags=["Positions"])
@require_permission(Permission.VIEW_PNL)
async def get_realtime_pnl(
    user: dict[str, Any] = Depends(_build_user_context),
) -> RealtimePnLResponse:
    """
    Get real-time P&L with latest market prices.

    Fetches latest prices from Redis cache (populated by Market Data Service).
    Falls back to database prices if real-time data is unavailable.

    Price source priority:
    1. real-time: Latest price from Redis (Market Data Service via WebSocket)
    2. database: Last known price from database (closing price or last fill)
    3. fallback: Entry price (if no other price available)

    Returns:
        RealtimePnLResponse with real-time P&L for all positions

    Examples:
        >>> import requests
        >>> response = requests.get("http://localhost:8002/api/v1/positions/pnl/realtime")
        >>> response.json()
        {
            "positions": [
                {
                    "symbol": "AAPL",
                    "qty": "10",
                    "avg_entry_price": "150.00",
                    "current_price": "152.50",
                    "price_source": "real-time",
                    "unrealized_pl": "25.00",
                    "unrealized_pl_pct": "1.67",
                    "last_price_update": "2024-10-19T14:30:15Z"
                }
            ],
            "total_positions": 1,
            "total_unrealized_pl": "25.00",
            "total_unrealized_pl_pct": "1.67",
            "realtime_prices_available": 1,
            "timestamp": "2024-10-19T14:30:20Z"
        }
    """
    # Resolve strategy access (fail closed)
    authorized_strategies = get_authorized_strategies(user.get("user"))
    if not authorized_strategies and not has_permission(
        user.get("user"), Permission.VIEW_ALL_STRATEGIES
    ):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="No strategy access")

    # DESIGN DECISION: Separate try/except for DB call vs empty-result guard.
    # The first handles database exceptions (connection failures, query errors).
    # The second handles the business logic case where query succeeds but returns
    # no positions for strategy-scoped users. Merging them would conflate error
    # handling with normal empty-result flow. Alternative: single block with
    # isinstance checks, but reduces clarity of distinct failure modes.
    try:
        if has_permission(user.get("user"), Permission.VIEW_ALL_STRATEGIES):
            db_positions = db_client.get_all_positions()
        else:
            db_positions = db_client.get_positions_for_strategies(authorized_strategies)
    except Exception as exc:  # pragma: no cover - defensive for test env without DB
        logger.error(
            "Failed to load positions for real-time P&L",
            exc_info=True,
            extra={"error": str(exc)},
        )
        return RealtimePnLResponse(
            positions=[],
            total_positions=0,
            total_unrealized_pl=Decimal("0"),
            total_unrealized_pl_pct=None,
            realtime_prices_available=0,
            timestamp=datetime.now(UTC),
        )

    # Additional guard: if strategy-scoped request returns no positions but DB call succeeded
    if not has_permission(user.get("user"), Permission.VIEW_ALL_STRATEGIES) and not db_positions:
        return RealtimePnLResponse(
            positions=[],
            total_positions=0,
            total_unrealized_pl=Decimal("0"),
            total_unrealized_pl_pct=None,
            realtime_prices_available=0,
            timestamp=datetime.now(UTC),
        )

    if not db_positions:
        # Reset P&L gauges to 0 when no positions (prevent stale values)
        pnl_dollars.labels(type="unrealized").set(0)
        pnl_dollars.labels(type="realized").set(0)
        pnl_dollars.labels(type="total").set(0)
        return RealtimePnLResponse(
            positions=[],
            total_positions=0,
            total_unrealized_pl=Decimal("0"),
            total_unrealized_pl_pct=None,
            realtime_prices_available=0,
            timestamp=datetime.now(UTC),
        )

    # Batch fetch real-time prices for all symbols (solves N+1 query problem)
    symbols = [pos.symbol for pos in db_positions]
    realtime_prices = _batch_fetch_realtime_prices_from_redis(symbols, redis_client)

    # Calculate real-time P&L for each position
    realtime_positions = []
    realtime_count = 0
    total_investment = Decimal("0")

    for pos in db_positions:
        # Using .get() for safer access (though all symbols should be in dict from batch fetch)
        realtime_price_data = realtime_prices.get(pos.symbol, (None, None))

        # Resolve price and calculate P&L (extracted for modularity)
        position_pnl, is_realtime = _resolve_and_calculate_pnl(pos, realtime_price_data)

        if is_realtime:
            realtime_count += 1

        realtime_positions.append(position_pnl)

        # Track total investment for portfolio-level percentage
        total_investment += pos.avg_entry_price * abs(pos.qty)

    # Calculate totals
    total_unrealized_pl = sum((p.unrealized_pl for p in realtime_positions), Decimal("0"))
    total_unrealized_pl_pct = (
        (total_unrealized_pl / total_investment) * Decimal("100") if total_investment > 0 else None
    )

    # Update P&L metrics
    # Note: Using total unrealized from realtime calculation, not database values
    total_realized_pl = sum((pos.realized_pl for pos in db_positions), Decimal("0"))
    pnl_dollars.labels(type="unrealized").set(float(total_unrealized_pl))
    pnl_dollars.labels(type="realized").set(float(total_realized_pl))
    pnl_dollars.labels(type="total").set(float(total_unrealized_pl + total_realized_pl))

    return RealtimePnLResponse(
        positions=realtime_positions,
        total_positions=len(realtime_positions),
        total_unrealized_pl=total_unrealized_pl,
        total_unrealized_pl_pct=total_unrealized_pl_pct,
        realtime_prices_available=realtime_count,
        timestamp=datetime.now(UTC),
    )


@app.post("/api/v1/webhooks/orders", tags=["Webhooks"])
async def order_webhook(request: Request) -> dict[str, str]:
    """Webhook for Alpaca order updates with per-fill P&L and row locking."""
    try:
        # Parse webhook payload
        body = await request.body()
        payload = await request.json()

        # Verify webhook signature (required when secret configured)
        if WEBHOOK_SECRET:
            signature_header = request.headers.get("X-Alpaca-Signature")
            signature = extract_signature_from_header(signature_header)

            if not signature:
                logger.warning("Webhook received without signature")
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing webhook signature"
                )

            if not verify_webhook_signature(body, signature, WEBHOOK_SECRET):
                logger.error("Webhook signature verification failed")
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid webhook signature"
                )

            logger.debug("Webhook signature verified successfully")
        else:
            logger.warning("Webhook signature verification disabled (WEBHOOK_SECRET not set)")

        logger.info(
            f"Webhook received: {payload.get('event', 'unknown')}", extra={"payload": payload}
        )

        # Extract order information
        event_type = payload.get("event")

        # Track webhook metrics
        webhook_received_total.labels(event_type=event_type or "unknown").inc()
        order_data = payload.get("order", {})

        client_order_id = order_data.get("client_order_id")
        broker_order_id = order_data.get("id")
        order_status = order_data.get("status")
        filled_qty = order_data.get("filled_qty")
        filled_avg_price = order_data.get("filled_avg_price")

        if not client_order_id:
            logger.warning("Webhook missing client_order_id")
            return {"status": "ignored", "reason": "missing_client_order_id"}

        # Fast path: if no fill info, just update status and return
        if event_type not in ("fill", "partial_fill") or not filled_qty or not filled_avg_price:
            broker_updated_at = _parse_webhook_timestamp(
                order_data.get("updated_at"),
                payload.get("timestamp"),
                order_data.get("created_at"),
                default=datetime.now(UTC),
            )

            updated_order = db_client.update_order_status_cas(
                client_order_id=client_order_id,
                status=order_status,
                broker_updated_at=broker_updated_at,
                status_rank=status_rank_for(order_status or ""),
                source_priority=SOURCE_PRIORITY_WEBHOOK,
                filled_qty=Decimal(str(filled_qty)) if filled_qty else Decimal("0"),
                filled_avg_price=Decimal(str(filled_avg_price)) if filled_avg_price else None,
                filled_at=None,
                broker_order_id=broker_order_id,
                broker_event_id=payload.get("execution_id"),
            )
            if not updated_order:
                logger.warning(f"Order not found for webhook or CAS skipped: {client_order_id}")
                return {"status": "ignored", "reason": "order_not_found"}
            return {"status": "ok", "client_order_id": client_order_id}

        # Fill processing: transactional with row locks
        filled_qty_dec = Decimal(str(filled_qty))
        filled_avg_price_dec = Decimal(str(filled_avg_price))

        per_fill_price = Decimal(str(payload.get("price", filled_avg_price_dec)))

        # Parse fill and broker timestamps using helper
        server_now = datetime.now(UTC)
        fill_timestamp = _parse_webhook_timestamp(
            payload.get("timestamp"),
            payload.get("filled_at"),
            order_data.get("filled_at"),
            default=server_now,
        )

        broker_updated_at = _parse_webhook_timestamp(
            order_data.get("updated_at"),
            payload.get("timestamp"),
            order_data.get("filled_at"),
            default=fill_timestamp,
        )

        with db_client.transaction() as conn:
            order = db_client.get_order_for_update(client_order_id, conn)
            if not order:
                logger.warning(f"Order not found for webhook: {client_order_id}")
                return {"status": "ignored", "reason": "order_not_found"}

            # Use Decimal values but compute integer delta from cumulative quantities
            # This ensures fractional fills accumulate at integer boundaries
            # e.g., 0.3 + 0.4 + 0.3 = 1.0 triggers a position update when crossing 1
            prev_filled_qty_dec = order.filled_qty or Decimal("0")
            incremental_fill_qty_int = int(filled_qty_dec) - int(prev_filled_qty_dec)

            # Log fractional remainder for observability (positions table uses integers)
            fractional_current = filled_qty_dec % 1
            fractional_prev = prev_filled_qty_dec % 1
            if fractional_current != 0 or fractional_prev != 0:
                logger.info(
                    "Fractional fill quantities detected; position updates at integer boundaries",
                    extra={
                        "client_order_id": client_order_id,
                        "filled_qty_decimal": str(filled_qty_dec),
                        "prev_filled_qty_decimal": str(prev_filled_qty_dec),
                        "incremental_fill_int": incremental_fill_qty_int,
                        "fractional_current": str(fractional_current),
                        "fractional_prev": str(fractional_prev),
                    },
                )

            # Only update position and append fill metadata if there's an incremental fill
            if incremental_fill_qty_int > 0:
                position_locked = db_client.get_position_for_update(order.symbol, conn)
                old_realized = position_locked.realized_pl if position_locked else Decimal("0")

                position = db_client.update_position_on_fill_with_conn(
                    symbol=order.symbol,
                    fill_qty=incremental_fill_qty_int,
                    fill_price=per_fill_price,
                    side=order.side,
                    conn=conn,
                )

                realized_delta = position.realized_pl - old_realized

                db_client.append_fill_to_order_metadata(
                    client_order_id=client_order_id,
                    fill_data={
                        "fill_id": f"{client_order_id}_{int(filled_qty_dec)}",
                        "fill_qty": incremental_fill_qty_int,
                        "fill_price": str(per_fill_price),
                        "realized_pl": str(realized_delta),
                        "timestamp": fill_timestamp.isoformat(),
                    },
                    conn=conn,
                )
            else:
                logger.info(
                    "No incremental fill; skipping position update but still updating order status",
                    extra={
                        "client_order_id": client_order_id,
                        "prev_filled_qty": str(prev_filled_qty_dec),
                        "current_filled_qty": str(filled_qty_dec),
                        "order_status": order_status,
                    },
                )

            # Always update order status/avg_price (even with no incremental fill)
            # This ensures status-only updates and price corrections are captured
            db_client.update_order_status_with_conn(
                client_order_id=client_order_id,
                status=order_status,
                filled_qty=filled_qty_dec,
                filled_avg_price=filled_avg_price_dec,
                filled_at=fill_timestamp if order_status == "filled" else None,
                conn=conn,
                broker_order_id=broker_order_id,
                broker_updated_at=broker_updated_at,
                status_rank=status_rank_for(order_status or ""),
                source_priority=SOURCE_PRIORITY_WEBHOOK,
                broker_event_id=payload.get("execution_id"),
            )

        # Invalidate performance cache after successful fill
        _invalidate_performance_cache(trade_date=fill_timestamp.date())

        return {"status": "ok", "client_order_id": client_order_id}

    except HTTPException:
        # Re-raise HTTPException with its original status code (e.g., 401 from signature validation)
        raise
    except Exception as e:
        logger.error(f"Webhook processing error: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=f"Webhook processing failed: {str(e)}"
        ) from e


# ============================================================================
# Startup / Shutdown
# ============================================================================


@app.on_event("startup")
async def startup_event() -> None:
    """Application startup."""
    logger.info("Execution Gateway started")
    logger.info(f"DRY_RUN mode: {DRY_RUN}")
    logger.info(f"Strategy ID: {STRATEGY_ID}")

    settings = get_settings()
    if settings.internal_token_required:
        secret_value = settings.internal_token_secret.get_secret_value()
        if not secret_value:
            logger.warning(
                "INTERNAL_TOKEN_REQUIRED=true but INTERNAL_TOKEN_SECRET is not configured",
            )

    # Open async database pool for auth/session validation
    # Note: open() is idempotent - safe to call multiple times
    from apps.execution_gateway.api.dependencies import get_db_pool

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
    slice_scheduler = recovery_manager.slice_scheduler
    if slice_scheduler:
        # Guard against restarting a shutdown scheduler (APScheduler limitation)
        # After shutdown(), APScheduler cannot be restarted; this prevents errors
        # in test scenarios or app reloads where startup is called multiple times
        if not slice_scheduler.scheduler.running:
            slice_scheduler.start()
            logger.info("Slice scheduler started")
        else:
            logger.info("Slice scheduler already running (skipping start)")
    else:
        logger.warning("Slice scheduler not available (not started)")

    # Start reconciliation service (startup gating + periodic sync)
    global reconciliation_service, reconciliation_task
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


@app.on_event("shutdown")
async def shutdown_event() -> None:
    """Application shutdown."""
    logger.info("Execution Gateway shutting down")

    # Shutdown slice scheduler (wait for running jobs to complete)
    slice_scheduler = recovery_manager.slice_scheduler
    if slice_scheduler:
        logger.info("Shutting down slice scheduler...")
        slice_scheduler.shutdown(wait=True)
        logger.info("Slice scheduler shutdown complete")

    # Close async database pool for auth/session validation
    # Note: close() is idempotent - safe to call multiple times
    from apps.execution_gateway.api.dependencies import get_db_pool

    async_db_pool = get_db_pool()
    await async_db_pool.close()
    logger.info("Async database pool closed")

    # H2 Fix: Close database connection pool for clean shutdown
    db_client.close()
    logger.info("Database connection pool closed")

    # Stop reconciliation task
    if reconciliation_service:
        reconciliation_service.stop()
    if reconciliation_task:
        reconciliation_task.cancel()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "apps.execution_gateway.main:app",
        host="0.0.0.0",
        port=8002,
        reload=True,
        log_level=LOG_LEVEL.lower(),
    )

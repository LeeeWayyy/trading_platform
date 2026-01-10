"""
Web Console Configuration.

Centralized configuration for the Web Console backend utilities, including
execution gateway API URLs, authentication settings, and UI defaults.

Environment Variables:
    EXECUTION_GATEWAY_URL: Base URL for execution gateway API
    WEB_CONSOLE_AUTH_TYPE: Authentication type (basic, oauth2, dev)
    WEB_CONSOLE_USER: Username for basic auth (dev mode only)
    WEB_CONSOLE_PASSWORD: Password for basic auth (dev mode only)
    DATABASE_URL: PostgreSQL connection string for audit log
    SESSION_TIMEOUT_MINUTES: Session idle timeout (default: 15)
"""

import logging
import os
from typing import Literal

# ============================================================================
# API Configuration
# ============================================================================

EXECUTION_GATEWAY_URL = os.getenv("EXECUTION_GATEWAY_URL", "http://localhost:8002")

# API endpoints
ENDPOINTS = {
    "health": f"{EXECUTION_GATEWAY_URL}/health",
    "positions": f"{EXECUTION_GATEWAY_URL}/api/v1/positions",
    "pnl_realtime": f"{EXECUTION_GATEWAY_URL}/api/v1/positions/pnl/realtime",
    "account_info": f"{EXECUTION_GATEWAY_URL}/api/v1/account",
    "submit_order": f"{EXECUTION_GATEWAY_URL}/api/v1/orders",
    "kill_switch_status": f"{EXECUTION_GATEWAY_URL}/api/v1/kill-switch/status",
    "kill_switch_engage": f"{EXECUTION_GATEWAY_URL}/api/v1/kill-switch/engage",
    "kill_switch_disengage": f"{EXECUTION_GATEWAY_URL}/api/v1/kill-switch/disengage",
    "config": f"{EXECUTION_GATEWAY_URL}/api/v1/config",
    "performance_daily": f"{EXECUTION_GATEWAY_URL}/api/v1/performance/daily",
}

# ============================================================================
# Health Monitor Configuration (T7.2)
# ============================================================================

FEATURE_HEALTH_MONITOR = os.getenv("FEATURE_HEALTH_MONITOR", "true").lower() == "true"

SERVICE_URLS: dict[str, str] = {
    "orchestrator": os.getenv("ORCHESTRATOR_URL", "http://localhost:8003"),
    "signal_service": os.getenv("SIGNAL_SERVICE_URL", "http://localhost:8001"),
    "execution_gateway": os.getenv("EXECUTION_GATEWAY_URL", "http://localhost:8002"),
    "market_data_service": os.getenv("MARKET_DATA_SERVICE_URL", "http://localhost:8004"),
    "model_registry": os.getenv("MODEL_REGISTRY_URL", "http://localhost:8005"),
    "reconciler": os.getenv("RECONCILER_URL", "http://localhost:8006"),
    "risk_manager": os.getenv("RISK_MANAGER_URL", "http://localhost:8007"),
    # TODO: Enable after deploying metrics_server.py sidecar
}

PROMETHEUS_URL = os.getenv("PROMETHEUS_URL", "http://localhost:9090")

# ============================================================================
# Authentication Configuration
# ============================================================================

AUTH_TYPE: Literal["basic", "oauth2", "dev", "mtls"] = os.getenv(  # type: ignore
    "WEB_CONSOLE_AUTH_TYPE", "dev"
)

# Basic auth credentials (dev mode only)
DEV_USER = os.getenv("WEB_CONSOLE_USER", "admin")
DEV_PASSWORD = os.getenv("WEB_CONSOLE_PASSWORD", "admin")
DEV_ROLE = os.getenv("WEB_CONSOLE_DEV_ROLE", "admin")
DEV_USER_ID = os.getenv("WEB_CONSOLE_DEV_USER_ID", "") or DEV_USER
DEV_SESSION_VERSION = int(os.getenv("WEB_CONSOLE_DEV_SESSION_VERSION", "1"))
DEV_STRATEGIES = [
    s.strip() for s in os.getenv("WEB_CONSOLE_DEV_STRATEGIES", "").split(",") if s.strip()
]
if not DEV_STRATEGIES:
    default_strategy = os.getenv("STRATEGY_ID", "").strip()
    if default_strategy:
        DEV_STRATEGIES = [default_strategy]

# Session configuration
SESSION_TIMEOUT_MINUTES = int(os.getenv("SESSION_TIMEOUT_MINUTES", "15"))
SESSION_ABSOLUTE_TIMEOUT_HOURS = int(os.getenv("SESSION_ABSOLUTE_TIMEOUT_HOURS", "4"))

# IP address tracking for audit log
# Comma-separated list of trusted proxy IPs (e.g., "10.0.0.1,10.0.0.2")
# If set, X-Forwarded-For header will be trusted for requests from these IPs
# M6 Fix: Uses shared get_trusted_proxy_ips() from libs/common/network_utils
# Dev/test environments get safe localhost defaults (127.0.0.1, ::1)
from libs.common.network_utils import get_trusted_proxy_ips  # noqa: E402

TRUSTED_PROXY_IPS = get_trusted_proxy_ips()

# ============================================================================
# Database Configuration (for audit log)
# ============================================================================

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://trader:trader@localhost:5433/trader")

# ============================================================================
# API Request Configuration
# ============================================================================

# API request timeout (seconds)
API_REQUEST_TIMEOUT = 5

# Database connection timeout (seconds) - short to prevent blocking kill switch/auth
DATABASE_CONNECT_TIMEOUT = 2

# ============================================================================
# UI Configuration
# ============================================================================

# Auto-refresh interval (seconds) - configurable via environment variable
AUTO_REFRESH_INTERVAL = int(os.getenv("AUTO_REFRESH_INTERVAL_SECONDS", "10"))

# Minimum reason length for manual actions (characters)
MIN_REASON_LENGTH = 10

# Rate limiting configuration
RATE_LIMIT_THRESHOLD_1 = 3  # First threshold: 3 failed attempts
RATE_LIMIT_LOCKOUT_1 = 30  # Lockout duration: 30 seconds
RATE_LIMIT_THRESHOLD_2 = 5  # Second threshold: 5 failed attempts
RATE_LIMIT_LOCKOUT_2 = 300  # Lockout duration: 5 minutes
RATE_LIMIT_THRESHOLD_3 = 7  # Third threshold: 7+ failed attempts
RATE_LIMIT_LOCKOUT_3 = 900  # Lockout duration: 15 minutes

# Page title and layout
PAGE_TITLE = "Trading Platform - Web Console"
PAGE_ICON = "📈"
LAYOUT: Literal["centered", "wide"] = "wide"

# Audit log display configuration
AUDIT_LOG_DISPLAY_LIMIT = 10
AUDIT_LOG_DETAILS_TRUNCATE_LENGTH = 100

# ============================================================================
# Database Connection Pool Configuration (M7 Fix)
# ============================================================================

# Pool size configuration - defaults optimized for per-session model
# Each session may share the pool via module-level singleton
DB_POOL_MIN_SIZE = int(os.getenv("DB_POOL_MIN_SIZE", "2"))
DB_POOL_MAX_SIZE = int(os.getenv("DB_POOL_MAX_SIZE", "10"))

# Pool timeout configuration (seconds)
# How long to wait for a connection from the pool before raising error
DB_POOL_TIMEOUT = float(os.getenv("DB_POOL_TIMEOUT", "5.0"))


# ============================================================================
# Risk Dashboard Configuration
# ============================================================================


def _safe_float(env_var: str, default: float) -> float:
    """Safely parse float from environment variable.

    Returns default if env var is missing or malformed.

    Args:
        env_var: Environment variable name
        default: Default value if env var not set or invalid

    Returns:
        Parsed float or default
    """
    value = os.getenv(env_var)
    if value is None:
        return default
    try:
        return float(value)
    except (ValueError, TypeError):
        logging.getLogger(__name__).warning(
            f"Invalid float value for {env_var}: {value!r}, using default {default}"
        )
        return default


# Risk budget limits (daily VaR as fraction)
RISK_BUDGET_VAR_LIMIT = _safe_float("RISK_BUDGET_VAR_LIMIT", 0.05)  # 5% daily VaR limit
RISK_BUDGET_WARNING_THRESHOLD = _safe_float(
    "RISK_BUDGET_WARNING_THRESHOLD", 0.8
)  # 80% utilization warning

# Feature flag for risk dashboard (T6.3)
FEATURE_RISK_DASHBOARD = os.getenv("FEATURE_RISK_DASHBOARD", "false").lower() in {
    "1",
    "true",
    "yes",
    "on",
}

# Strategy comparison feature flag (T6.4b)
FEATURE_STRATEGY_COMPARISON = os.getenv("FEATURE_STRATEGY_COMPARISON", "false").lower() in {
    "1",
    "true",
    "yes",
    "on",
}

# ============================================================================
# Manual Controls Configuration (T6.6)
# ============================================================================

# Manual controls API base URL (separate from existing endpoints for clarity)
MANUAL_CONTROLS_API_BASE = f"{EXECUTION_GATEWAY_URL}/api/v1"

# Feature flag for manual trade controls (T6.6)
FEATURE_MANUAL_CONTROLS = os.getenv("FEATURE_MANUAL_CONTROLS", "false").lower() in {
    "1",
    "true",
    "yes",
    "on",
}

# Minimum reason length for flatten-all action (stricter than regular actions)
MIN_FLATTEN_ALL_REASON_LENGTH = 20

# MFA step-up token maximum age in seconds
# Must match backend verify_2fa_token max age (60s in apps/execution_gateway/api/dependencies.py)
# Using slightly lower value (55s) to account for network/clock skew
MFA_STEP_UP_MAX_AGE_SECONDS = 55

# Feature flag for backtest manager (T5.3)
FEATURE_BACKTEST_MANAGER = os.getenv("FEATURE_BACKTEST_MANAGER", "false").lower() in {
    "1",
    "true",
    "yes",
    "on",
}

# ============================================================================
# Circuit Breaker Dashboard Configuration (T7.1)
# ============================================================================

# Feature flag for circuit breaker dashboard (T7.1)
FEATURE_CIRCUIT_BREAKER = os.getenv("FEATURE_CIRCUIT_BREAKER", "false").lower() in {
    "1",
    "true",
    "yes",
    "on",
}

# Feature flag for alert configuration UI (T7.3)
FEATURE_ALERTS = os.getenv("FEATURE_ALERTS", "false").lower() in {
    "1",
    "true",
    "yes",
    "on",
}

# Minimum reason length for circuit breaker reset
MIN_CIRCUIT_BREAKER_RESET_REASON_LENGTH = 20

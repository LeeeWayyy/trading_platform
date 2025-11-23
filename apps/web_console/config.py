"""
Web Console Configuration.

Centralized configuration for the Streamlit web console, including
execution gateway API URLs, authentication settings, and UI defaults.

Environment Variables:
    EXECUTION_GATEWAY_URL: Base URL for execution gateway API
    WEB_CONSOLE_AUTH_TYPE: Authentication type (basic, oauth2, dev)
    WEB_CONSOLE_USER: Username for basic auth (dev mode only)
    WEB_CONSOLE_PASSWORD: Password for basic auth (dev mode only)
    DATABASE_URL: PostgreSQL connection string for audit log
    SESSION_TIMEOUT_MINUTES: Session idle timeout (default: 15)
"""

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
    "submit_order": f"{EXECUTION_GATEWAY_URL}/api/v1/orders",
    "kill_switch_status": f"{EXECUTION_GATEWAY_URL}/api/v1/kill-switch/status",
    "kill_switch_engage": f"{EXECUTION_GATEWAY_URL}/api/v1/kill-switch/engage",
    "kill_switch_disengage": f"{EXECUTION_GATEWAY_URL}/api/v1/kill-switch/disengage",
    "config": f"{EXECUTION_GATEWAY_URL}/api/v1/config",
}

# ============================================================================
# Authentication Configuration
# ============================================================================

AUTH_TYPE: Literal["basic", "oauth2", "dev", "mtls"] = os.getenv(  # type: ignore
    "WEB_CONSOLE_AUTH_TYPE", "dev"
)

# Basic auth credentials (dev mode only)
DEV_USER = os.getenv("WEB_CONSOLE_USER", "admin")
DEV_PASSWORD = os.getenv("WEB_CONSOLE_PASSWORD", "admin")

# Session configuration
SESSION_TIMEOUT_MINUTES = int(os.getenv("SESSION_TIMEOUT_MINUTES", "15"))
SESSION_ABSOLUTE_TIMEOUT_HOURS = int(os.getenv("SESSION_ABSOLUTE_TIMEOUT_HOURS", "4"))

# IP address tracking for audit log
# Comma-separated list of trusted proxy IPs (e.g., "10.0.0.1,10.0.0.2")
# If set, X-Forwarded-For header will be trusted for requests from these IPs
# If not set, all audit log entries will show "localhost" (safe default for dev)
TRUSTED_PROXY_IPS = [
    ip.strip() for ip in os.getenv("TRUSTED_PROXY_IPS", "").split(",") if ip.strip()
]

# ============================================================================
# Database Configuration (for audit log)
# ============================================================================

DATABASE_URL = os.getenv(
    "DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/trading_platform"
)

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

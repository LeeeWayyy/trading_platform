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

EXECUTION_GATEWAY_URL = os.getenv(
    "EXECUTION_GATEWAY_URL", "http://localhost:8002"
)

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

AUTH_TYPE: Literal["basic", "oauth2", "dev"] = os.getenv(  # type: ignore
    "WEB_CONSOLE_AUTH_TYPE", "dev"
)

# Basic auth credentials (dev mode only)
DEV_USER = os.getenv("WEB_CONSOLE_USER", "admin")
DEV_PASSWORD = os.getenv("WEB_CONSOLE_PASSWORD", "admin")

# Session configuration
SESSION_TIMEOUT_MINUTES = int(os.getenv("SESSION_TIMEOUT_MINUTES", "15"))
SESSION_ABSOLUTE_TIMEOUT_HOURS = int(os.getenv("SESSION_ABSOLUTE_TIMEOUT_HOURS", "4"))

# ============================================================================
# Database Configuration (for audit log)
# ============================================================================

DATABASE_URL = os.getenv(
    "DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/trading_platform"
)

# ============================================================================
# UI Configuration
# ============================================================================

# Auto-refresh interval (seconds)
AUTO_REFRESH_INTERVAL = 10

# Page title and layout
PAGE_TITLE = "Trading Platform - Web Console"
PAGE_ICON = "📈"
LAYOUT: Literal["centered", "wide"] = "wide"

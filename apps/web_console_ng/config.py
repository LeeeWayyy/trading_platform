"""NiceGUI Web Console configuration.

Minimal C0 configuration for the NiceGUI-based web console. Later components
extend this with session store, auth middleware, and audit logging settings.
"""

from __future__ import annotations

import base64
import binascii
import ipaddress
import logging
import math
import os
from typing import Literal, cast

# =============================================================================
# Server settings
# =============================================================================

HOST = os.getenv("WEB_CONSOLE_NG_HOST", "0.0.0.0")
PORT = int(os.getenv("WEB_CONSOLE_NG_PORT", "8080"))
DEBUG = os.getenv("WEB_CONSOLE_NG_DEBUG", "false").lower() in {"1", "true", "yes", "on"}
PAGE_TITLE = os.getenv("WEB_CONSOLE_NG_PAGE_TITLE", "Trading Platform - Web Console (NiceGUI)")
POD_NAME = os.getenv("POD_NAME", "nicegui-0")

logger = logging.getLogger(__name__)

# =============================================================================
# Health Check & Observability
# =============================================================================

HEALTH_CHECK_BACKEND_ENABLED = os.getenv("HEALTH_CHECK_BACKEND_ENABLED", "false").lower() in {
    "1",
    "true",
    "yes",
    "on",
}

METRICS_INGRESS_PROTECTED = os.getenv("METRICS_INGRESS_PROTECTED", "false").lower() in {
    "1",
    "true",
    "yes",
    "on",
}

INTERNAL_PROBE_TOKEN = os.getenv("INTERNAL_PROBE_TOKEN", "").strip()
INTERNAL_PROBE_DISABLE_IP_FALLBACK = os.getenv(
    "INTERNAL_PROBE_DISABLE_IP_FALLBACK", "false"
).lower() in {"1", "true", "yes", "on"}

# =============================================================================
# Admission Control / Connection Limits
# =============================================================================

# Global max WebSocket connections per pod (semaphore-enforced)
WS_MAX_CONNECTIONS = int(os.getenv("WS_MAX_CONNECTIONS", "1000"))

# Max concurrent connections per authenticated session (Redis-enforced)
WS_MAX_CONNECTIONS_PER_SESSION = int(os.getenv("WS_MAX_CONNECTIONS_PER_SESSION", "2"))

# TTL for session connection counter keys in Redis (seconds)
WS_SESSION_CONN_TTL = int(os.getenv("WS_SESSION_CONN_TTL", "3600"))

# Timeout for session validation during WebSocket admission (seconds)
WS_SESSION_VALIDATION_TIMEOUT = float(os.getenv("WS_SESSION_VALIDATION_TIMEOUT", "2.0"))

# =============================================================================
# Backend endpoints
# =============================================================================

EXECUTION_GATEWAY_URL = os.getenv("EXECUTION_GATEWAY_URL", "http://localhost:8002")

# =============================================================================
# Dashboard polling intervals (seconds)
# =============================================================================

DASHBOARD_MARKET_POLL_SECONDS = float(os.getenv("DASHBOARD_MARKET_POLL_SECONDS", "5.0"))
DASHBOARD_STALE_CHECK_SECONDS = float(os.getenv("DASHBOARD_STALE_CHECK_SECONDS", "10.0"))

# =============================================================================
# Redis
# =============================================================================

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/1")

# Redis Sentinel / HA Configuration
REDIS_USE_SENTINEL = os.getenv("REDIS_USE_SENTINEL", "false").lower() in {
    "1",
    "true",
    "yes",
    "on",
}
REDIS_MASTER_NAME = os.getenv("REDIS_MASTER_NAME", "nicegui-sessions")
REDIS_PASSWORD = os.getenv("REDIS_PASSWORD", "")
REDIS_SENTINEL_PASSWORD = os.getenv("REDIS_SENTINEL_PASSWORD", REDIS_PASSWORD)
REDIS_POOL_MAX_CONNECTIONS = int(os.getenv("REDIS_POOL_MAX_CONNECTIONS", "200"))

_RAW_SENTINEL_HOSTS = os.getenv("REDIS_SENTINEL_HOSTS", "").strip()
REDIS_SENTINEL_HOSTS: list[tuple[str, int]] = []
if _RAW_SENTINEL_HOSTS:
    for entry in _RAW_SENTINEL_HOSTS.split(","):
        host, port = entry.strip().split(":")
        REDIS_SENTINEL_HOSTS.append((host, int(port)))
else:
    # Default for local dev if Sentinel enabled but no hosts
    if REDIS_USE_SENTINEL:
        REDIS_SENTINEL_HOSTS = [("localhost", 26379)]

# Redis SSL/TLS Configuration
REDIS_SSL_ENABLED = os.getenv("REDIS_SSL_ENABLED", "false").lower() in {
    "1",
    "true",
    "yes",
    "on",
}
REDIS_SENTINEL_SSL_ENABLED = os.getenv(
    "REDIS_SENTINEL_SSL_ENABLED",
    "true" if REDIS_SSL_ENABLED else "false",
).lower() in {"1", "true", "yes", "on"}
REDIS_SSL_CA_CERTS = os.getenv("REDIS_SSL_CA_CERTS", "")
REDIS_SSL_CERTFILE = os.getenv("REDIS_SSL_CERTFILE", "")
REDIS_SSL_KEYFILE = os.getenv("REDIS_SSL_KEYFILE", "")
REDIS_SSL_CERT_REQS = os.getenv("REDIS_SSL_CERT_REQS", "required").lower()

# =============================================================================
# Session configuration (timeouts + cookies)
# =============================================================================

SESSION_IDLE_TIMEOUT_MINUTES = int(os.getenv("SESSION_IDLE_TIMEOUT_MINUTES", "15"))
SESSION_ABSOLUTE_TIMEOUT_HOURS = int(os.getenv("SESSION_ABSOLUTE_TIMEOUT_HOURS", "4"))

SESSION_COOKIE_SECURE = os.getenv(
    "SESSION_COOKIE_SECURE",
    "true" if not DEBUG else "false",
).lower() in {
    "1",
    "true",
    "yes",
    "on",
}
SESSION_COOKIE_SAMESITE = os.getenv("SESSION_COOKIE_SAMESITE", "lax").lower()
if SESSION_COOKIE_SAMESITE not in {"lax", "strict", "none"}:
    raise ValueError("SESSION_COOKIE_SAMESITE must be one of: lax, strict, none")
SESSION_COOKIE_PATH = os.getenv("SESSION_COOKIE_PATH", "/")
SESSION_COOKIE_DOMAIN = os.getenv("SESSION_COOKIE_DOMAIN", "") or None
SESSION_COOKIE_HTTPONLY = os.getenv("SESSION_COOKIE_HTTPONLY", "true").lower() in {
    "1",
    "true",
    "yes",
    "on",
}
SESSION_COOKIE_NAME = "__Host-nicegui_session" if SESSION_COOKIE_SECURE else "nicegui_session"

# =============================================================================
# Device binding + trusted proxies
# =============================================================================

DEVICE_BINDING_ENABLED = os.getenv(
    "DEVICE_BINDING_ENABLED",
    "true" if SESSION_COOKIE_SECURE else "false",
).lower() in {"1", "true", "yes", "on"}

DEVICE_BINDING_SUBNET_MASK = int(os.getenv("DEVICE_BINDING_SUBNET_MASK", "24"))

_RAW_TRUSTED_PROXIES = os.getenv("TRUSTED_PROXY_IPS", "").strip()
TrustedProxy = (
    ipaddress.IPv4Network | ipaddress.IPv6Network | ipaddress.IPv4Address | ipaddress.IPv6Address
)

TRUSTED_PROXY_IPS: list[TrustedProxy] = []
if _RAW_TRUSTED_PROXIES:
    for entry in _RAW_TRUSTED_PROXIES.split(","):
        value = entry.strip()
        if not value:
            continue
        try:
            if "/" in value:
                TRUSTED_PROXY_IPS.append(ipaddress.ip_network(value, strict=False))
            else:
                TRUSTED_PROXY_IPS.append(ipaddress.ip_address(value))
        except ValueError:
            raise ValueError(f"Invalid TRUSTED_PROXY_IPS entry: {value}") from None

# =============================================================================
# Trusted hosts
# =============================================================================

_RAW_ALLOWED_HOSTS = os.getenv("ALLOWED_HOSTS", "localhost,127.0.0.1,::1")
if "*" in {h.strip() for h in _RAW_ALLOWED_HOSTS.split(",")}:
    ALLOWED_HOSTS = ["*"]
else:
    ALLOWED_HOSTS = [h.strip() for h in _RAW_ALLOWED_HOSTS.split(",") if h.strip()]

# =============================================================================
# Authentication configuration
# =============================================================================


def _load_auth_type() -> Literal["dev", "basic", "mtls", "oauth2"]:
    """Load and validate auth type.

    SECURITY: In production (DEBUG=False), AUTH_TYPE must be explicitly set.
    'dev' auth type is only allowed when DEBUG=True.
    """
    value = os.getenv("WEB_CONSOLE_AUTH_TYPE", "").lower()
    allowed = {"dev", "basic", "mtls", "oauth2"}

    # SECURITY: Require explicit AUTH_TYPE in production
    if not value:
        if DEBUG:
            value = "dev"  # Allow implicit dev mode only in DEBUG
            logger.warning("AUTH_TYPE not set, defaulting to 'dev' (DEBUG mode only)")
        else:
            raise ValueError(
                "WEB_CONSOLE_AUTH_TYPE must be explicitly set in production. "
                "Valid options: basic, mtls, oauth2"
            )

    if value not in allowed:
        raise ValueError("WEB_CONSOLE_AUTH_TYPE must be one of: dev, basic, mtls, oauth2")

    # SECURITY: 'dev' auth type requires DEBUG mode
    if value == "dev" and not DEBUG:
        raise ValueError(
            "AUTH_TYPE='dev' is not allowed in production (DEBUG=False). "
            "Use basic, mtls, or oauth2 for production deployments."
        )

    return cast(Literal["dev", "basic", "mtls", "oauth2"], value)


AUTH_TYPE = _load_auth_type()
# UI flag for login page to show/hide selector
SHOW_AUTH_TYPE_SELECTOR = os.getenv("SHOW_AUTH_TYPE_SELECTOR", "false").lower() in {
    "1",
    "true",
    "yes",
    "on",
}
ALLOW_DEV_BASIC_AUTH = os.getenv("WEB_CONSOLE_ALLOW_DEV_BASIC_AUTH", "false").lower() in {
    "1",
    "true",
    "yes",
    "on",
}
if ALLOW_DEV_BASIC_AUTH:
    logger.warning("WEB_CONSOLE_ALLOW_DEV_BASIC_AUTH is enabled; dev credentials are active.")

DEV_USER_ID = os.getenv("WEB_CONSOLE_DEV_USER_ID", "dev-user")
DEV_ROLE = os.getenv("WEB_CONSOLE_DEV_ROLE", "admin")
DEV_STRATEGIES = [
    s.strip() for s in os.getenv("WEB_CONSOLE_DEV_STRATEGIES", "").split(",") if s.strip()
]
if not DEV_STRATEGIES:
    default_strategy = os.getenv("STRATEGY_ID", "").strip()
    if default_strategy:
        DEV_STRATEGIES = [default_strategy]

# =============================================================================
# OAuth2/OIDC Configuration
# =============================================================================
# These are only required when AUTH_TYPE=oauth2 (validated in oauth2.py)

OAUTH2_CLIENT_ID = os.getenv("OAUTH2_CLIENT_ID", "")
OAUTH2_CLIENT_SECRET = os.getenv("OAUTH2_CLIENT_SECRET", "")
OAUTH2_AUTHORIZE_URL = os.getenv("OAUTH2_AUTHORIZE_URL", "")
OAUTH2_TOKEN_URL = os.getenv("OAUTH2_TOKEN_URL", "")
OAUTH2_USERINFO_URL = os.getenv("OAUTH2_USERINFO_URL", "")
OAUTH2_CALLBACK_URL = os.getenv("OAUTH2_CALLBACK_URL", "")
OAUTH2_ISSUER = os.getenv("OAUTH2_ISSUER", "")
# Optional - for RP-initiated logout
OAUTH2_LOGOUT_URL = os.getenv("OAUTH2_LOGOUT_URL", "")
OAUTH2_POST_LOGOUT_REDIRECT_URL = os.getenv("OAUTH2_POST_LOGOUT_REDIRECT_URL", "")

# =============================================================================
# Audit logging
# =============================================================================

AUDIT_LOG_DB_ENABLED = os.getenv(
    "AUDIT_LOG_DB_ENABLED", "true" if not DEBUG else "false"
).lower() in {"1", "true", "yes", "on"}

AUDIT_LOG_SINK = os.getenv("AUDIT_LOG_SINK", "both" if not DEBUG else "log").lower()
if AUDIT_LOG_SINK not in {"log", "db", "both"}:
    raise ValueError("AUDIT_LOG_SINK must be one of: log, db, both")

AUDIT_LOG_RETENTION_DAYS = int(os.getenv("AUDIT_LOG_RETENTION_DAYS", "90"))

# =============================================================================
# Encryption / signing keys (C2 wiring)
# =============================================================================

SESSION_ENCRYPTION_KEY = os.getenv("SESSION_ENCRYPTION_KEY", "").strip()
SESSION_ENCRYPTION_KEY_PREV = os.getenv("SESSION_ENCRYPTION_KEY_PREV", "").strip()

HMAC_SIGNING_KEYS = os.getenv("HMAC_SIGNING_KEYS", "").strip()
HMAC_CURRENT_KEY_ID = os.getenv("HMAC_CURRENT_KEY_ID", "").strip()


def _decode_base64_key(value: str, env_name: str) -> bytes:
    if not value:
        raise ValueError(f"{env_name} environment variable not set")
    try:
        key_bytes = base64.b64decode(value)
    except Exception as exc:  # pragma: no cover - defensive
        raise ValueError(f"{env_name} must be base64-encoded: {exc}") from exc
    if len(key_bytes) != 32:
        raise ValueError(f"{env_name} must decode to 32 bytes (got {len(key_bytes)})")
    return key_bytes


def _decode_hex_key(value: str, env_name: str) -> bytes:
    if not value:
        raise ValueError(f"{env_name} value is empty")
    try:
        return binascii.unhexlify(value)
    except (binascii.Error, ValueError) as exc:
        raise ValueError(f"{env_name} must be hex-encoded: {exc}") from exc


def get_encryption_keys() -> list[bytes]:
    """Return encryption keys in priority order (current -> previous)."""

    keys = [_decode_base64_key(SESSION_ENCRYPTION_KEY, "SESSION_ENCRYPTION_KEY")]
    if SESSION_ENCRYPTION_KEY_PREV:
        keys.append(_decode_base64_key(SESSION_ENCRYPTION_KEY_PREV, "SESSION_ENCRYPTION_KEY_PREV"))
    return keys


def get_signing_keys() -> dict[str, bytes]:
    """Return signing keys indexed by key id.

    Format: HMAC_SIGNING_KEYS="01:abcd...,02:dead..." (hex-encoded keys)
    """

    if not HMAC_SIGNING_KEYS:
        raise ValueError("HMAC_SIGNING_KEYS environment variable not set")

    key_map: dict[str, bytes] = {}
    for pair in HMAC_SIGNING_KEYS.split(","):
        pair = pair.strip()
        if not pair:
            continue
        if ":" not in pair:
            raise ValueError("HMAC_SIGNING_KEYS must be in format 'id:key' separated by commas")
        key_id, key_hex = pair.split(":", 1)
        key_id = key_id.strip()
        key_hex = key_hex.strip()
        if not key_id:
            raise ValueError("HMAC_SIGNING_KEYS entry missing key id")
        key_map[key_id] = _decode_hex_key(key_hex, f"HMAC_SIGNING_KEYS[{key_id}]")

    if not key_map:
        raise ValueError("HMAC_SIGNING_KEYS must contain at least one key")

    if HMAC_CURRENT_KEY_ID and HMAC_CURRENT_KEY_ID not in key_map:
        raise ValueError("HMAC_CURRENT_KEY_ID does not match any key id in HMAC_SIGNING_KEYS")

    return key_map


# =============================================================================
# Risk Dashboard Configuration (P5T6)
# =============================================================================
# PARITY: Defaults match apps/web_console/config.py:87-89 (Streamlit config)


def _parse_float(env_var: str, default: float) -> float:
    """Parse float from environment variable with fallback.

    Rejects NaN/inf values to prevent poison data in risk calculations.
    """
    raw = os.getenv(env_var, "").strip()
    if not raw:
        return default
    try:
        value = float(raw)
        if not math.isfinite(value):
            logger.warning(f"Non-finite {env_var} value '{raw}', using default {default}")
            return default
        return value
    except ValueError:
        logger.warning(f"Invalid {env_var} value '{raw}', using default {default}")
        return default


# Defaults from Streamlit: apps/web_console/config.py:87-89
RISK_BUDGET_VAR_LIMIT = _parse_float("RISK_BUDGET_VAR_LIMIT", 0.05)  # 5% daily VaR limit (parity)
RISK_BUDGET_WARNING_THRESHOLD = _parse_float(
    "RISK_BUDGET_WARNING_THRESHOLD", 0.8
)  # 80% warning (parity)

FEATURE_RISK_DASHBOARD = os.getenv("FEATURE_RISK_DASHBOARD", "false").lower() in {
    "1",
    "true",
    "yes",
    "on",
}
